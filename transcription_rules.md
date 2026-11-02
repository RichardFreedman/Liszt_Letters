# Project instructions: manuscript letter transcription

## Purpose and corpus

This project transcribes archival scans of manuscript letters into Markdown. Each input image is one scanned page: a page of a letter, or the front or back of an envelope. The material is mostly nineteenth-century correspondence, chiefly in German (often in Kurrent script, with Latin script for names, foreign words and titles), and some letters may be in French or other languages. Scans often include a colour target, grey scale or ruler beside the document. Ignore these: they are not part of the document.

The transcripts are archival source material for musicological research. Fidelity to the source outranks speed, polish and everything else.

Never translate. Never modernise. Transcribe every word in the language and spelling of the original.

## Output structure

**One Markdown file per document item on the image.** Normally an image shows one item: one letter page, or one side of an envelope. If an image shows several distinct items (for example an envelope next to a letter leaf, or two separate notes), each one gets its own file, numbered in reading order: left to right, then top to bottom. Two facing pages of the same letter shown in one image are one item: transcribe them in one file with page markers (see below).

**Do not join pages across images.** A letter continuing from one image to the next stays in separate files, one per image, even mid-sentence.

**Metadata fields.** Fill these from what is written on the item itself, never from general knowledge, handwriting recognition, neighbouring images or what would make sense historically. If a field is not written on this item, leave it empty. A continuation page with no date, salutation or signature gets empty fields; do not carry them over from another page.

- `document_type`: one of `letter`, `envelope`, `postcard`, `note`, `other`. Use `letter` for any page of a letter, including continuation pages.
- `sender`: the name as signed at the end of a letter, or as written in a sender's line on an envelope, in the original spelling. Empty if there is no signature or sender line on this item.
- `recipient`: the addressee as written in the envelope address or in an inside address or salutation, in the original spelling, without honorifics (`Herrn`, `Hochwohlgeboren`, `Monsieur`). A salutation that gives no name, such as `Lieber Freund`, leaves it empty.
- `place`: the place written in the date line of a letter, or the destination town of an envelope address, in the original spelling.
- `date`: the date in ISO form if written in full (`1867-03-14`); partial as `1867-03` or `1867`. Convert only what is written: `14. März 67` becomes `1867-03-14` only if the century is unambiguous from the item itself; otherwise give only what is certain and flag it. Empty if no date is written.
- `date_source`: where the date comes from: `written` (in the writer's text), `postmark` (from a legible postmark), or `absent`.
- `title`: a short English label built only from the fields above, for example `Letter to Gottschalg, Weimar, 1867-03-14, page 1`, `Envelope addressed to Herrn Gottschalg, Tiefurt`, or `Letter, continuation page`. This is the only place where English editorial wording is allowed outside the markers below.
- `language`: the main language of the text, in English (`German`, `French`).
- `needs_review`: true if anything in the file is flagged for review.

**Body structure**, in this order:

1. `# {title}`
2. One description line in square brackets, factual and brief: the type of item, the writing medium and script, and the layout. Example: `[Description: envelope, front; brown ink, Kurrent script, address written diagonally; red wax seal remnant at upper left.]` Do not interpret, date or attribute the hand.
3. `## Transcription`, with the diplomatic transcription.
4. `## Marginalia and other hands`, only if there are any (see below).
5. `## For review`, only if anything is flagged.

## Diplomatic transcription

Reproduce the text exactly as written:

- **Keep the original lineation.** Each written line goes on its own line of the Markdown file. Leave a blank line only where the original has a paragraph break or a visible gap.
- **Keep spelling, capitalisation, punctuation and accents exactly**, including period spellings (`Thür`, `seyn`, `giebt`), inconsistent spellings and slips of the pen. Do not correct, do not modernise, do not insert *sic*.
- **Keep words divided at line ends as written**, with the division sign the writer used (`-` or `=`). Do not rejoin them.
- **Do not expand abbreviations.** Transcribe `Hochwohlgeb.`, `ergebenst.`, `d.` or `Ew.` as written. Raised letters go in `<sup>`: `Hochwohlgeb<sup>n</sup>`, `M<sup>r</sup>`.
- **Transcribe Kurrent and Latin script both in ordinary letters.** Do not mark the change of script.
- **Doubled consonant strokes** (the bar over `m` or `n`) are transcribed as the single letter with a combining macron: `kom̄en`. Write the letter twice only if the writer did.
- **Underlining:** `<u>text</u>`; double underlining `<u><u>text</u></u>`.
- **Deletions:** `~~text~~`. A deletion that cannot be read: `~~[illegible]~~`.
- **Insertions by the writer:** `[ins. above: text]`, `[ins. below: text]` or `[ins. margin: text]`, placed at the point where the insertion belongs.
- **Page markers:** where one image shows more than one page of the same letter, mark each on its own line: `[page 1]`, `[page 2]`, following the order of the writing. Any page number the writer wrote is transcribed as part of the text, where it stands.

**Letters:** transcribe every part in its position and order: the place and date line, any inside address, the salutation, the body, the closing formula, the signature, and any postscript. Text the writer wrote crosswise, upside down or in the margins to continue the letter is transcribed after the main text, preceded by a marker saying where it is: `[written crosswise across page 1:]`, `[written upside down at the top of the page:]`.

**Envelopes:** transcribe the address in its lineation, including honorifics and directions (`Herrn`, `Wohlgeboren`, `frei`, `per Adresse`). Describe postmarks, stamps and seals in brackets where they stand, and transcribe any text on them that is legible: `[postmark: WEIMAR 14 3 67]`. A partly legible postmark uses the uncertainty markers. The back of an envelope gets its own file.

## Uncertain readings

**Never invent a word.** This is the governing rule. Where text is not legible, insert a marker rather than a guess:

- `[illegible]`: no reading possible. Add an extent if it can be judged: `[illegible, c. 3 words]`, `[illegible, 1 line]`.
- `[?Gottschalg?]`: a best guess supported by visible letterforms and context. The guess must be plausible from what is actually visible, not merely from what would make sense.
- `[?Tiefurt? / ?Tieffurt?]`: two competing readings.
- `[damaged: c. 2 words]`: text lost to a tear, hole, stain, fold or binding.

A silent guess is a corrupted source; a marker is an honest gap. When in doubt, mark.

**Proper nouns, dates and numbers need the most care.** Names of people, places, works and institutions, and all dates and figures, are the research payload. Check each one against the image letter by letter. Do not "correct" a name to its familiar modern form: if the writer spells a name two ways, transcribe both as written. If a name is only partly legible, use the guess marker rather than completing it from general knowledge.

## Marginalia and other hands

Record everything on the item that the writer did not write as part of the letter in the section `## Marginalia and other hands`, one entry per line. Each entry says where it is and what it looks like, then gives the text in quotation marks:

- `[left margin, beside line 5; pencil, different hand]: "text"`
- `[top right; pencil]: archival folio number "23"`
- `[lower left; red pencil]: a short diagonal stroke, no text`
- `[centre bottom; purple stamp]: "GOETHE- UND SCHILLER-ARCHIV"`

This covers recipients' notes, later annotations, archive foliation and shelf marks, stamps and dockets. Use the uncertainty markers inside the quoted text. Where a marginal note is keyed to a point in the text (by a sign such as `x` or `+`, or by its position), put `[see margin note 1]` at that point in the transcription and number the entries in this section to match.

Marginal additions the writer made as part of the letter are not marginalia: transcribe them as insertions or as crosswise text, as described above.

## Musical notation, drawings and figures

Do not transcribe music as pitches unless every note is unmistakable. Instead, describe the notation in brackets where it occurs in the text, so a reader knows what is there and where:

`[Musical notation: one staff, 4 bars; treble clef, key signature of 2 flats, 3/4; melody with slurs; text underlay "Ave Maria"; dynamic "p" under bar 1.]`

State what is clearly visible: the number of staves and bars, clefs, key and time signatures, tempo and expression words (transcribed exactly), text underlay (transcribed exactly), and whether it looks like a melody, chords or a sketch. Mark anything uncertain with `?`. If individual notes are unmistakable you may add them (`opening notes: g' a' b♭'`), but leave them out rather than guess.

Describe drawings, diagrams, monograms, seals and other figures the same way: `[Drawing: small sketch of a keyboard, about 5 cm wide, in the left margin.]` Describe only what is visible; do not interpret it.

## Quality self-check

Before finishing each item, re-read the transcription against the image once. Attend to the usual Kurrent confusions (e/n, u/n, h/f, s/ſ/h, st/ft), to each proper noun, date and number, and to the line count: every written line must appear once, in order. Anything that survives the check at less than high confidence gets flagged, not silently accepted.

**Do not describe or interpret beyond what these rules allow.** No summaries, no commentary on content, no identification of hands or people, no notes on historical significance. The only editorial matter permitted in a file is the metadata, the title, the description line, the bracketed markers defined above, the marginalia section and the `For review` section.

## Flagging for review

Flag, rather than resolve, anything below high confidence:

- any use of an `[illegible]`, `[?…?]` or `[damaged]` marker
- proper nouns, dates or numbers where the reading is plausible but not certain
- a date whose century or year had to be worked out rather than read
- uncertain marginalia, postmarks or stamps
- musical notation or figures whose description is uncertain

Flagged files set `needs_review` to true and end with a section `## For review` listing each flagged passage, with enough surrounding text to locate it and a note on what the doubt is. Omit the section entirely when nothing is flagged.

## Unusable images

If the image is too blurred, dark, faint or cropped to transcribe responsibly, do not attempt a partial reconstruction. Report it as not usable, with the reason. A blank page, or a page with only archival marks, is usable: transcribe it as an item with an empty `## Transcription` section, describe it, and record the marks under marginalia.

Never stop to ask a question. Every ambiguity has a resolution that needs no input: uncertain readings get markers, uncertain metadata gets flagged, unusable images get reported. Choose the conservative option, record it, and continue.
