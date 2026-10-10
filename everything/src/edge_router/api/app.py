from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse

from edge_router.config import Settings
from edge_router.observability.logging import configure_logging, log_event
from edge_router.routing.policy import PolicyConfig, RoutingPolicy
from edge_router.schemas import ChatRequest, DecisionRequest
from edge_router.service.orchestrator import Orchestrator


def build_orchestrator(settings: Settings) -> Orchestrator:
    if settings.model_backend == "tiny":
        from edge_router.model.tiny_backend import TinyBackend

        backend = TinyBackend(settings.max_context_tokens)
    elif settings.model_backend == "gemma4_strands":
        from edge_router.model.gemma4_strands_backend import build_gemma4_strands_backend

        backend = build_gemma4_strands_backend(settings)
    else:
        from edge_router.model.gemma3_backend import build_backend

        backend = build_backend(settings)
    if settings.decision_checkpoint:
        from edge_router.federation.checkpoint_manager import load_checkpoint

        backend.head_manager.activate(
            load_checkpoint(settings.decision_checkpoint, backend.identity)
        )
    return Orchestrator(
        backend,
        RoutingPolicy(PolicyConfig.load(settings.policy_config)),
        default_max_new_tokens=settings.default_max_new_tokens,
        unsafe_content_logging=settings.unsafe_content_logging,
    )


def create_app(
    orchestrator: Orchestrator | None = None, settings: Settings | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configure_logging(settings.log_level)
        app.state.orchestrator = orchestrator or build_orchestrator(settings)
        yield

    app = FastAPI(title="Shared-prefill Edge Router", version="1.0", lifespan=lifespan)
    if orchestrator is not None:
        app.state.orchestrator = orchestrator

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # FastAPI's default response includes invalid input values, possibly secrets.
        return JSONResponse(status_code=422, content={"detail": "Invalid request schema"})

    @app.exception_handler(ValueError)
    async def input_error(request: Request, exc: ValueError):
        return JSONResponse(
            status_code=400, content={"detail": "Invalid prompt or generation limits"}
        )

    @app.middleware("http")
    async def sanitize_execution_errors(request: Request, call_next):
        # Exception handlers for Exception are re-raised by Starlette to the ASGI
        # server, which can log tokenizer exceptions containing prompt text.
        try:
            return await call_next(request)
        except Exception:
            log_event("request_failed", error_code="LOCAL_EXECUTION_FAILED")
            return JSONResponse(status_code=500, content={"detail": "Local execution failed"})

    @app.get("/health")
    def health():
        return {"status": "ok", "model_loaded": hasattr(app.state, "orchestrator")}

    @app.get("/v1/model/info")
    def info():
        return app.state.orchestrator.backend.model_info()

    @app.post("/v1/decision")
    def decision(request: DecisionRequest):
        return app.state.orchestrator.decide(request)

    @app.post("/v1/chat")
    def chat(request: ChatRequest):
        service = app.state.orchestrator
        if not request.stream:
            return service.run(request)

        def events():
            # FastAPI may resume synchronous generators on different worker threads.
            # Own the complete model generator in one dedicated producer thread.
            from edge_router.api.streaming import threaded_events

            yield from threaded_events(service, request)

        return StreamingResponse(events(), media_type="text/event-stream")

    return app
