# Difficulty head evaluation

## Difficulty (test)

| head | Spearman | Pearson | RMSE | within-dataset Spearman |
|---|---|---|---|---|
| mlp | 0.750 | 0.751 | 0.662 | 0.393 |
| ridge | 0.751 | 0.752 | 0.656 | 0.351 |

MLP - ridge Spearman: -0.001 (under 0.02: the MLP adds little over ridge)
Leave-one-dataset-out mean Spearman: MLP 0.257, ridge 0.257
Correctness AUC from -d: Qwen3-8B 0.739, glm-4.6 0.830, gpt-5 0.800

## Layers

L_d=10 (best 20, cost 0.003 Spearman), prefill needed for routing: 0.458 of 24 layers

## Coverage (LLMRouterBench items excluded by the length cap)

- aime: 0/60 (0.0%)
- arc-agi: 28/400 (7.0%)
- arcc: 0/1170 (0.0%)
- arenahard: 1/750 (0.1%)
- bbh: 0/1080 (0.0%)
- emorynlp: 0/696 (0.0%)
- finqa: 0/1138 (0.0%)
- gpqa: 0/198 (0.0%)
- hle: 4/2158 (0.2%)
- humaneval: 0/164 (0.0%)
- kandk: 0/700 (0.0%)
- korbench: 0/1210 (0.0%)
- livecodebench: 0/1055 (0.0%)
- livemathbench: 0/121 (0.0%)
- math500: 0/500 (0.0%)
- mathbench: 0/150 (0.0%)
- mbpp: 0/970 (0.0%)
- medqa: 0/1273 (0.0%)
- meld: 0/1231 (0.0%)
- mmlupro: 0/2964 (0.0%)
- simpleqa: 0/4326 (0.0%)
- swe-bench: 341/500 (68.2%)
- tau2: 0/167 (0.0%)
- winogrande: 0/1267 (0.0%)

## Timing

```
{
 "quantize": {
  "step": "quantize",
  "time": "2026-10-11 09:47:21",
  "seconds": 141.2,
  "quantize_seconds": 134.2,
  "peak_gpu_gb": 8.3
 },
 "pilot_features": {
  "step": "pilot_features",
  "time": "2026-10-11 09:52:04",
  "seconds": 49.7
 },
 "pilot": {
  "step": "pilot",
  "time": "2026-10-11 09:52:04",
  "seconds": 279.9,
  "tok_s_per_gpu": 27785,
  "peak_gpu_gb": 85.5
 },
 "train": {
  "step": "train",
  "time": "2026-10-11 10:01:12",
  "seconds": 7.2,
  "peak_gpu_gb": 0.5
 },
 "extract": {
  "wall_seconds": 293.8,
  "total_tokens": 9188153,
  "tok_s_total": 31273.495575221237,
  "tok_s_per_gpu": [
   31276
  ],
  "peak_gpu_gb": 59.5,
  "storage_gb": 0.3
 }
}
```