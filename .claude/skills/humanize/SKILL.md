---
name: humanize
description: Rewrite prose so it reads as written by a person rather than an assistant. Use on documentation, comments, pull request text or release notes, and when someone says a file sounds robotic or AI-written.
---

# Humanize

## Load the voice

Read `voice/register.txt`, `voice/constructions.md`, and the persona named for this surface in `.claude/review-profile.md` under *Voice*. Default to `voice/personas/house.md`.

A persona applies to text that posts under a person's name. Public documentation stays on the house voice unless the profile says otherwise.

## Rewrite, do not substitute

The register list catches words. The constructions file catches shape, and shape is the actual tell. Swapping a flagged word for a synonym leaves the sentence exactly as machine-written as it was.

Work in this order:

1. **Punctuation.** Every em dash becomes a comma, a colon, parentheses or a full stop. The full stop is usually right and is the one people forget.
2. **Sentence length.** Read the paragraph and count. If every sentence lands between fifteen and twenty-five words, break one in half and let another run long.
3. **Paragraph openers.** If three paragraphs in a row start with a bolded pronouncement, keep the strongest and rewrite the others to open with the example, the exception, or the question the reader already has.
4. **Constructions.** Negative parallelism, forced tricolons, copula avoidance, participle chains, false ranges, elegant variation. Each one is named in `voice/constructions.md` with what to write instead.
5. **Register.** Now do the word list, last, on what is left.

## Preserve the claims

This is a rewrite of the prose, not of the facts. Every technical claim, number, file path, identifier and obligation keyword survives unchanged. If a sentence is wrong, say so separately rather than fixing it silently while rewriting the voice.

Where a sentence turns out to say nothing once the decoration is gone, delete it and say you did.

## Check it

Run the prose hook over each file you touched and report a clean result, or say which hits you left and why. Read one paragraph aloud in your head: if you never run out of breath and never stop early, it is still flat.
