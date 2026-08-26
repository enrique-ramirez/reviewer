# Shareability

> Seed content. Replace the specifics with your own line on when something
> belongs in a shared package.

Shareability is about whether the next person solving this problem finds the
solution, or writes it again. Both directions are worth catching: code that
should be shared and is not, and code that has been made shareable before anyone
knows what shape it needs.

## Something already exists

Before a new helper, hook, component, or utility lands, the question is whether
this codebase already has one. Search for it — by name, by the thing it does, by
the type it returns. Where you find one, name it and say where it is; the author
usually did not know it was there.

Near-duplicates are worth as much attention as exact ones. Two date formatters
with slightly different edge-case behaviour is a bug that will take someone a
day to find.

## Something should be shared

A third occurrence of the same pattern is usually the point at which it earns a
home. Two is a coincidence; three is a shape.

Weigh this against the cost of the move. Pulling something into a shared package
adds an import boundary, a version, and a blast radius. Where the shared version
would need three flags to serve its callers, the duplication was carrying real
differences and is doing its job.

When you suggest extracting something, say where it goes and what its interface
is. *"Consider extracting this"* leaves the author with all the work.

## Something is shared too early

A component that will only ever have one caller does not need to live in a
shared package. An abstraction built for requirements nobody has asked for yet
tends to be wrong about them, and is harder to change than the duplication it
replaced.

Where a shared thing has grown a boolean flag that changes its behaviour
substantially, that is usually two things wearing one name.

## Shared means someone else's problem now

Code in a shared package is used by callers the author cannot see. That raises
the bar for it: the API should be hard to misuse, the types should carry the
constraints, the failure modes should be explicit, and a breaking change should
be visible as one.

Where a change alters the behaviour of something already shared, trace who calls
it, and say what happens to them.
