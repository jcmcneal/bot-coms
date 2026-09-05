# Assignment checklists

The assigning peer creates a completion checklist for each assignment before
dispatch. Define what must come back, not a permanent checklist for a role. Use
the actual frozen return peer and accountable owner; reporting relationships are
per task. These are agent operating instructions, not additional transport or
ledger enforcement.

## Create and delegate

Put the checklist in the assignment letter with the outcome and constraints.
Give each item a stable ID, responsible peer, concrete completion condition and
required evidence. Include only requirements applicable to this task and its
authorized scope. Do not add a universal software delivery sequence to research,
coordination or other work.

When delegating, the assignee becomes the assigner of the child and writes its
checklist. Set `parent_slice` and map child items to the parent obligations they
support. Delegation or a child's completion does not by itself satisfy the parent;
the parent assignee remains responsible for collecting and verifying the result.
Keep undelegated obligations on the parent checklist.

## Receive, report and verify

- On receipt, read the checklist and resolve missing or ambiguous requirements
  through the frozen return peer before dependent work. Continue unblocked work.
- On a child result, failure recovery or return handoff, revisit every item.
  Report `satisfied`, `blocked` or `not attempted`, with evidence for satisfied
  items and a reason, next action and responsible peer for the others.
- Read required saved artifacts before claiming satisfaction. Output saying an
  artifact was produced is not a substitute for the artifact. Evidence must match
  the assigned work and current submission revision.
- The receiving peer verifies the reported items before treating its obligations
  as complete. Report remaining gaps explicitly; a receipt, process exit or partial
  result is not checklist completion.
- Checklist verification supplements the frozen review gates and accountable
  owner's `team_workflow accept`; it cannot replace either or grant new authority.
  If return peer and owner differ, route the verified result to the owner through
  the existing workflow.

## Keep requirements stable

Checklist requirements belong above `## Notes` in the frozen assignment. Do not
tick boxes or edit requirements there after dispatch: doing so changes the content
digest. Record progress and item results in `## Notes`, referenced artifacts and
`team_report` evidence. Use the existing new-assignment or owner-authorized
reassignment procedure when requirements change. An assignee cannot waive an
item by declaring it inapplicable; obtain the assigning peer's disposition, subject
to the frozen contract and owner authority.

For an older assignment without a checklist, derive a proposed item mapping from
its existing acceptance criteria and return it to the assigning peer. Record the
agreed mapping outside the frozen specification; do not invent extra requirements
or mutate an in-flight letter.

## Assignment-letter pattern

Adapt the items to the task; the example is not a prescribed workflow.

```markdown
## Completion checklist

| ID | Responsible peer | Completion condition | Required evidence | Parent item |
|---|---|---|---|---|
| C1 | <assignee> | <observable requested outcome> | <artifact/check and identity> | <ID or none> |

## Notes

### Checklist result

| ID | Status | Evidence or blocker | Next action / responsible peer |
|---|---|---|---|
| C1 | not attempted | Work has not started | <action> / <peer> |
```

Provisioning installs access to this shared policy and verifies it with a bounded
assignment. Actual task checklists are created by assigning peers when work exists;
do not provision speculative tasks or copy live checklists into profile memories.
