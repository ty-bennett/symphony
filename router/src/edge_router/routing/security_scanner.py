import re

from edge_router.schemas import Message


class SecurityScanner:
    """Conservative pattern escalation, not a complete DLP system. Returns codes only."""

    RULES = {
        "PRIVATE_KEY": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----",
        "AWS_ACCESS_KEY": r"\b(?:AKIA|ASIA|AIDA|AROA)[A-Z0-9]{16}\b",
        "BEARER_TOKEN": r"\bbearer\s+[^\s,;\"']{8,}",
        "LABELLED_SECRET": (
            r"\b(?:password|passwd|pwd|api[_-]?key|api[_-]?token|access[_-]?token|"
            r"refresh[_-]?token|client[_-]?secret|aws[_-]?secret[_-]?access[_-]?key|"
            r"secret[_-]?key|authorization)\b[\"']?\s*[:=]\s*[\"']?[^\s\"',;}{]{3,}"
        ),
        "SSN_PATTERN": r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)",
        "CREDENTIAL_URL": r"\b[a-z][a-z0-9+.-]*://[^\s/:]+:[^\s/@]+@",
        "TOKEN_PATTERN": r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{16,})\b",
    }

    def __init__(self):
        self._rules = {
            code: re.compile(pattern, re.IGNORECASE) for code, pattern in self.RULES.items()
        }

    def scan(self, messages: list[Message]) -> list[str]:
        return sorted(
            {
                code
                for message in messages
                for code, regex in self._rules.items()
                if regex.search(message.content)
            }
        )
