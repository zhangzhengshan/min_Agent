---
name: facts-searching
license: MIT
compatibility: Requires web search and parallel subagents. Worker model selection is used when the host supports it.
description: Delegate web research to parallel subagents, synthesize their findings, and return only decision-relevant conclusions.
---

Act as a research lead rather than doing all web research yourself.

- Spawn multiple subagents in parallel for focused, independent, or complementary research tasks, and wait until all of them have finished before you synthesize or return anything. If your subagent mechanism offers a choice between blocking and background execution, choose blocking.
- Run search subagents on a faster, lower-cost model than your own whenever your harness lets you choose, whether through a model option on the subagent tool or a preconfigured cheaper worker type. A missing named route is not a reason to skip this. Pick a tier that is still capable of multi-source web research, and keep your own model unchanged.
- Give each subagent a clear research objective, but do not micromanage how it searches.
- Use additional follow-up research only when important conflicts or gaps remain.

After receiving the results:

- If a subagent fails or returns nothing, report the gap rather than filling it from your own prior knowledge.
- Deduplicate overlapping findings and sources.
- Resolve material source conflicts where possible.
- Distinguish established facts, reasonable inferences, and unresolved unknowns.
- Discard information that does not affect the current question or decision.
- Preserve only the sources needed to support the final conclusions.

Return a concise synthesized answer to the parent agent. Mark any conclusion not supported by a source a worker returned as unverified.

Do not return raw search logs, worker transcripts, or redundant intermediate findings.
