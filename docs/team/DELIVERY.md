# Delivery sequence

All implementation requires the independent reviews and owner acceptance selected by its workflow policy.

For UI work, the coordinating assignee arranges this sequence:

1. Design reviewer approves implementation mocks.
2. Implementer builds against those mocks.
3. Design reviewer signs off the implemented result.
4. Independent verifier performs QA.
5. Accountable owner accepts after the required gates and child work pass.

Use the `ui_delivery` policy for UI implementation. Resolve participants from the assignment contract and responsibility bindings. Record each stage and any exception with concrete board evidence.

## One landable slice at a time

Do **not** open a new delivery slice until the previous one has **landed on main**.

- One in-flight **landable** slice at a time per product.
- Parallel research/setup that cannot land is fine only when it does not create a second landable stream.
- Landed = merged to main (CI green + merge), not a green PR left open and not local tests alone.
