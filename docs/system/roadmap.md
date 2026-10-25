# Status and roadmap

This manual describes what is built on `master`. What is in progress and what comes next is tracked on GitHub, not here, so that it cannot go stale in two places.

| Where | What it holds |
|---|---|
| [Epics](https://github.com/AlessioNar/open-brick-designer/issues?q=is%3Aissue+label%3Aepic) | Large pieces of work, each with sub-issues |
| [Owner decisions](https://github.com/AlessioNar/open-brick-designer/issues?q=is%3Aissue+label%3Aowner-decision) | Questions waiting on the owner |
| [In progress](https://github.com/AlessioNar/open-brick-designer/issues?q=is%3Aissue+is%3Aopen+label%3Ain-progress) | Issues someone is working on now |
| [Plans index](../plans/README.md) | Design documents and their status: active, done, reference, superseded |

## How a plan becomes code

1. A plan or proposal is written under `docs/plans/active/` and discussed.
2. The owner's decisions are recorded in the plan and, when open, as `owner-decision` issues.
3. The work is split into an epic with sub-issues. Every change has its issue.
4. Pull requests close those issues. When the work is merged, the plan moves to `docs/plans/done/` and stays as the record, and the matching page in this manual is updated to describe how it works.

When a plan and the code disagree, the code is what runs, and this manual describes the code.
