# Front-end correctness

> Seed content. Replace the specifics with the ones you actually care about —
> the failures you have personally had to debug are worth more here than general
> best practice.

Front-end bugs mostly live in the gap between "it worked when I clicked it" and
"it runs a thousand times a day on other people's machines". Look at the paths
the author probably did not click.

## State and effects

Where a component fetches, subscribes, or writes on mount, follow the dependency
array to whether it settles. Object and array literals, inline functions, and
freshly-destructured context values are new every render, so an effect that
depends on one runs every render. Trace what that costs: a request per keystroke,
a subscription that stacks, a render loop.

Where an effect starts something async, look for how it ends: aborted on
unmount, guarded against a stale response overwriting a fresh one, cleaned up on
dependency change. The bug shows up as the wrong data appearing after a fast
navigation.

Where state is derived from props, check whether it stays in sync when the props
change.

## The states other than "success"

For anything that loads, look for all four: loading, empty, error, and success.
Empty is the one most often missed, and it is the one that renders `undefined` or
`0 results` where a person expected a message.

For anything a user submits, look for what happens on the second click, on a slow
response, and on a failure. Double submission is the common one.

## Types

TypeScript is only as useful as its boundaries. Where data comes in from an API,
a form, `localStorage`, or a URL parameter, look at whether it is validated or
just asserted. An `as` cast at a boundary is a runtime error waiting for the
first unexpected payload.

Where a union is switched on, check the default branch handles a member added
later.

## Rendering

Keys drawn from array index reorder wrongly when the list reorders — the symptom
is inputs keeping the wrong values after a sort. Watch for expensive work in
render that could sit outside it. Watch for lists that will be long in production
but are three items in the fixture.

## Time, money, and locale

Dates constructed without a timezone, currency in floats, string comparison for
sorting numbers, and hardcoded formats are all the sort of thing that works
locally and fails for a user in another country. Say what breaks and for whom.
