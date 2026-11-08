---
name: semantic-clarification
license: MIT
description: Identify and clarify terminology misuse, conceptual confusion, lexical ambiguity, unclear references, vague expressions, and context-dependent meanings in natural-language task descriptions. Triggered when the user requests "语义校准" or "术语对齐", or when the intended meaning of a task needs semantic alignment. Strictly focuses on semantic understanding and concludes by outputting only the clarified, normalized task description; strictly never executes, codes, or implements the task itself.
---

# Semantic Clarification — 任务语义澄清

## Objective

Identify and resolve terminology misuse, conceptual confusion, lexical ambiguity, unclear references, vague expressions, and context-dependent meanings in natural-language task descriptions. Establish a shared semantic understanding with the task requester through necessary clarification and terminology normalization, preventing task deviation caused by misinterpretation.


## Scope

- **Terminology Normalization**: Identify and correct the misuse, inconsistent use, or conflation of technical terms, ensuring consistent conceptual expression.


- **Semantic Disambiguation**: Identify expressions with multiple reasonable interpretations and determine their intended meaning and references.


- **Semantic Clarification**: Clarify expressions whose meanings are vague, whose references are unclear, or whose interpretation depends on specific contextual information.


## Core Boundaries

**Address only "What exactly does the requester mean?", not "What else must be specified to achieve the requester's objective?"**


Do not perform Requirements Clarification concerning the completeness or adequacy of the requirements themselves, or Requirements Elicitation aimed at uncovering unstated needs, constraints, or decisions.


Strictly distinguish between **established meaning, reasonable inference, and confirmed intent**. The model may propose plausible interpretations based on context, but must never treat an inference as the requester's confirmed intent.


## Guiding Principles

1. Identify substantive semantic issues that could lead to misinterpretation, considering the available context. Avoid overanalyzing clear expressions or introducing artificial ambiguities.


2. Normalize clear terminology errors and inconsistencies. When multiple reasonable interpretations exist, explain the key alternatives and their differences. Normalization must not alter the original intent without authorization; if the intended meaning is uncertain, mark proposed corrections as requiring confirmation.


3. Prefer resolving ambiguities using the available context. Ask targeted clarification questions only when the intended meaning cannot be reliably determined and different interpretations could materially affect the task's direction or outcome. Do not make semantic decisions on the requester's behalf.


4. When external information is needed to verify terminology, professional definitions, or context-dependent interpretations, delegate the research to a sub-agent using the `facts-searching` skill. External evidence may establish facts and terminology, but cannot substitute for confirmation of the requester's actual intent.


5. Clearly distinguish established meanings, recommended standardized expressions, and unresolved ambiguities in the output. When useful, provide a normalized task description faithful to the original intent, without expanding or modifying the requirements.


## Completion Criteria

The key terms and expressions in the task description have clear, consistent meanings that both parties can understand in the same way. Any semantic ambiguities that could materially affect the task's direction have either been resolved through confirmation or explicitly identified as awaiting confirmation.


If no substantive semantic issues are found, simply state that no clarification is necessary. Avoid unnecessary follow-up questions or requirements analysis.

