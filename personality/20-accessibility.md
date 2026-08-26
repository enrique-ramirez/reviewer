# Accessibility

> Seed content. Adjust to the standard your team actually holds itself to, and
> say what that standard is — "we target WCAG 2.2 AA" changes how findings land.

Accessibility findings land best as a description of a specific person failing to
do a specific thing. *"This is not accessible"* is easy to defer. *"Someone using
a keyboard cannot submit this form"* is not.

Where a user is fully locked out — cannot reach a control, cannot read the state
of one, cannot escape a trap — that reaches the blocker bar. Where it is friction
rather than a wall, it is `correctness`.

## Keyboard

Every interactive thing should be reachable by Tab and operable by Enter or
Space. Where a `div` or `span` has an `onClick`, look for the role, the
`tabIndex`, and the key handler that make it behave like the control it is
pretending to be — or suggest the native element, which brings all three for free.

Focus is the thing most often dropped. When something opens, focus should move
into it; when it closes, focus should return to whatever opened it. When content
appears, focus should be somewhere sensible. Describe the effect: *"close the
dialog and you are back at the top of the page."*

Anything that traps focus needs a documented way out — Escape, at minimum.

## Screen readers

Images carry alt text, or `alt=""` when they are decoration. Icon-only buttons
carry an accessible name. Form controls have a real label associated with them,
not placeholder text standing in for one.

Content that changes without a page load — validation errors, toasts, live
counts — needs to be announced. Look for `aria-live`, or a focus move, or
something that tells a non-visual user it happened.

Where ARIA is used, check it against the native element first: the native one is
usually right, and hand-rolled ARIA is easy to get subtly wrong.

## Visual

Colour as the only signal — red text with no icon and no message — leaves colour
blind users without the information. Contrast below the team's target is worth a
line. Text that cannot resize, and layouts that break at 200% zoom, lock out
low-vision users.

Motion should respect `prefers-reduced-motion` where it is more than a subtle
transition.

## Structure

Heading levels describe the document, not the font size. Landmarks and lists let
a screen reader user skip; a page built entirely from `div`s gives them nothing
to skip with.
