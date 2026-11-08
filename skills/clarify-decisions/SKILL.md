---
name: clarify-decisions
license: MIT
description: Clarify the decisions behind a plan, idea, requirement, or choice through dependency-ordered rounds of questions, until the user and agent share an understanding of what has been decided and what remains uncertain. Triggered when the user invokes "决策梳理" or explicitly requests clarify-decisions. Strictly focuses on discussion and decision mapping, and concludes by outputting a revised, rigorous version of the proposal/plan based on the decisions; strictly never proceeds to execution, coding, or implementation.
---

Work with the user toward a shared understanding of the decisions that shape what they brought — a plan, an idea, a requirement, a choice — sufficient for what it is meant to support. Stay at its level: its own decisions are in scope, but don't descend a layer (requirements into implementation, an idea into an execution plan) unless they ask. A lower-level choice they have already fixed is a constraint to record, not a design to expand.


Open with a short working understanding the user can correct, together with any questions already worth asking; when the subject is large, name its main open areas in one line. Stop to confirm intent on its own only when plausible readings would lead to fundamentally different tasks. Let depth follow what the result is for and what a wrong call would cost. Speak the user's language.


Decisions hang off other decisions. The **frontier** is every open decision whose prerequisites are settled: what you can ask now without guessing at answers you haven't heard. In each round, ask a manageable set of the most consequential questions from the frontier. If an answer could materially change another question's relevance, scope, or options, that question waits for a later round. Then wait for the user's answers and update the frontier from them.


Number the questions when there are several, so they can be answered by number. Give each the context it needs and the real trade-off. Offer options only when they are genuine and make the choice easier, and leave room for an answer you didn't list. When the evidence and the user's stated aims point to an answer, recommend it and say why; when the choice turns on their values, priorities, or tolerance for risk, lay out the trade-off and let them choose.


Ask only for what the user owns — something only they know, or a call that is theirs to make — and only when different answers would materially change the outcome. Settle low-impact, reversible details with a sensible default instead of asking. State any assumption that could materially change the conclusion, scope, feasibility, risk, recommendation, or how success is judged.




Finding facts is your job: never ask the user for anything you could look up yourself. When a decision needs a fact, read specific, known things directly; for anything that has to be searched for, dispatch a sub-agent and tell it which decision the fact serves: it explores the environment (files, tools, etc.) itself and uses the facts-searching skill for anything on the web. Without sub-agents, investigate yourself. Pause the clarification while research is running, and resume once the results are back and incorporated. What cannot be verified stays unknown or is stated as an assumption, never as fact, and a high-stakes or irreversible decision does not rest on one silently. When sources conflict, a source of truth the user has named takes precedence.


Look for what is missing, not only what is unclear: decisions the goal needs that nobody has raised. Industry habit alone is not a reason to add one. Raise an ambiguity only where it changes a decision, and a contradiction only where the constraints cannot all hold under the same conditions; name the conflict and let the user decide the compromise. When they ask you to challenge or stress-test the idea, also probe weak premises, counterexamples, failure modes, and alternative explanations — harder questions, same tone.


Keep the state where both of you can see it. When the user's answers change something, start your next reply with one line: what was settled, what reopened, what is still waiting. Reopen a settled decision only when new information would change it or undo what depends on it. When the work spans sessions, will be handed off, carries high stakes, or the user asks, keep one document as the current record, named and placed to fit the project.


When the decisions the intended use needs are settled, give a brief result: what it is for, the key decisions — with the reasoning for any that were hard to call or hard to undo — and the uncertainty that remains, distinguishing where it matters what the user decided, what was verified (and where), and what is assumed. Use a fuller format only if the user asks for a particular document. Clarification is complete once the user accepts the result and what remains open; ask for that only if they haven't already given it, and don't act on the result before they do. The skill ends there; what happens next is the user's call.

