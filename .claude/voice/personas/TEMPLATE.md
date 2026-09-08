# Persona template

Copy this to `voice/personas/<name>.md` and fill it in, then point `Voice` in a repository's `.claude/review-profile.md` at the name. A persona is for text that posts under a human's name: pull request comments, review replies, commit bodies. Public documentation should stay on the house voice unless there is a reason not to.

Build it from evidence, not from memory. The three best sources are the person's own commit subjects, their messages in chat, and any prose they wrote before an assistant was involved. Quote real examples into the file. A persona written from an impression of somebody produces an impression of a person.

## Sections to fill

**Who is speaking, and to whom.** Their role, and whether the reader is a colleague, a stranger or a maintainer. This sets how much shorthand is allowed.

**Sentence shape.** Typical length. Whether they front the point or build to it. Whether they use fragments.

**Vocabulary they actually use.** Words they reach for, with real examples. Words they never use.

**Spelling and mechanics.** Regional spelling, capitalisation habits, how they punctuate emphasis.

**Openers and closers.** How they start a message and how they end one. Most people have two or three of each.

**How they disagree, and how they concede.** The highest-value section for review comments, and the one people get wrong.

**What to imitate, and what not to.** State this explicitly. Rhythm, directness and word choice transfer. Typing errors, grammatical slips and anything traceable to a person's first language do not: reproducing those reads as mockery, and it lands badly on text that posts under their name.
