---
description: "Record Core Web Vitals benchmark: LCP, INP, CLS, TTFB, weight (W12, AMD-CORE-033)."
---

# /signal-benchmark — Benchmark (W12, AMD-CORE-033)

**Phase:** execution  
**AMD:** AMD-CORE-033  
**Wave:** W12

## Purpose
Records Core Web Vitals (LCP, INP, CLS, TTFB) and page weight for a URL. Provides a historical benchmark trail for regressions.

## Usage
`signalos signal-benchmark <url> --wave W [--lcp F] [--inp F] [--cls F] [--ttfb F] [--weight F] [--json]`

## Metrics
- **LCP** (ms): Largest Contentful Paint  
- **INP** (ms): Interaction to Next Paint  
- **CLS** (score): Cumulative Layout Shift  
- **TTFB** (ms): Time to First Byte  
- **weight_kb**: Page weight in kilobytes

## Storage
`.signalos/deploy/benchmarks.jsonl` — append-only benchmark index
