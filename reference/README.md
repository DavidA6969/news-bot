# The reference cut

**Make the next video like this one.** Not the same story, the same *build*:
the shape of the script, the way shots are chosen, the pacing, the pauses. The
files here are the real thing — the script that was actually spoken, the shot
list that was actually cut, the description that was actually written.

Everything below is measured off that build. Where a number is in `style.json`
it is named, because the style is what makes every video look the same and it
is not for a per-video decision.

    reference/script.md       the 35 beats as spoken
    reference/shots.txt       the 35 in-points, with what each one shows
    reference/description.txt what shipped with it

## The shape

| | |
| --- | --- |
| Length | **65.6s**, 35 beats, 24 sentences, 190 words |
| Shot length | median **1.87s**, shortest 0.83s, longest 2.80s |
| Read | **3.50 words a second** (`voice.kokoro_speed` 1.28) |
| Marked beats | 30 of 35 carry a delivery direction |

Three acts, and the middle one is the longest:

```
  1-3    the hook          about to kill the only friend she ever had
  4-11   how it started    finds it dying, saves it, names it
 12-19   the loss          it heals, it flies, something bigger takes it
 20-26   the hunt          deserts, mountains, years, a cave
 27-30   the act           it breathes fire, she is faster, she kills it
 31-35   the turn          the scar is one she tied — she killed her own
```

## The script

**The hook is the first sentence and it is the whole video in one line.**
"This girl is about to kill the only friend she has ever had." Twelve words,
under two seconds. `render.py retention` fails an opening beat that runs longer.

**The last line runs back into the first.** "…and she just killed him" against
"This girl is about to kill…". A loop turns one view into two.

**A beat is a clause, not a sentence.** Sentences are split across beats at
commas so the voice carries over the cut. 35 beats become 24 sentences.

**Sentences start the way people talk.** `And`, `But`, `So`, `Then`, `Until`,
`There's`. A sentence that opens cold reads as a caption.

**Two sentences on one line is a reversal, and it lands.** `It breathes fire.
She's faster.` `Not for weeks. For years.` The stop inside the line is 0.50s
and it is the strongest punctuation available. Use three or four in a video,
not ten.

**The twist is shown before it is said.** The scar appears on screen, then the
line that explains it, then "By her." Do not announce a reveal you are about
to show.

## The shots

**One clip per beat, chosen against its line, not against the light.** The
bandage shot goes under "splints the wing"; the fireball goes under "it
breathes fire". `pick_shots` scores what is worth looking at and has no idea
which shot is the dragon — for a story, pass the in-points by hand.

**Index the whole source first.** A 184-frame contact sheet of the film at 4s
intervals, then pick each beat off it. Choosing from memory of the film is how
the last version ended up with a tavern under "splinted once by somebody
guessing".

**Every in-point is snapped so a clip cannot straddle a cut** (`snap_to_shot`),
and each clip is cut as long as its own shot allows, capped at 5s. A clip
shorter than its beat is a build error, not a warning.

**Two callbacks, deliberately.** The bandaging returns on the same frame under
"splinted once by somebody guessing"; he returns flying under "he grew up while
she was gone". A repeated shot is only a mistake when it is accidental.

## The delivery

Marks go in the script, in braces, and are stripped before speaking:

```
16. takes him. And she can only watch. {faster, beat}
30. By her. {slower, beat}
```

Counted across this build: **4 `breath` (0.18s), 6 `hold` (0.34s), 4 `beat`
(0.6s)**, and 27 beats carrying a rate. Silence is the strongest of them.

**Never put a pause mark on a line that ends mid-sentence.** It ends the take,
so the sentence splits across two recordings and the first half comes back with
a rising, unfinished tune.

## What the style fixes, and you do not

These live in `style.json` and are the same in every video. Do not set them per
video; if one is wrong it is wrong for all of them.

| | |
| --- | --- |
| 1080x1920, 30fps, crossfade 0.12s, push-in 0.10 | the look |
| `am_michael` at 1.28, capped at `ARTICULATE_SPEED` | the voice |
| gap 0.26s, stop inside a line 0.50s, comma 0.13s, tail kept 140ms | the pauses |
| `min_luma` 52, `max_lift` 2.2 | dark footage is lifted for you |
| music: grief, 19 dB under the voice, ducking 7 dB | the bed |

## Before it goes out

```bash
python3 render.py fresh render.json        # footage this channel has not used
python3 render.py narration render.json    # varied enough to listen to
python3 render.py retention render.json    # shaped for the feed
python3 render.py rights render.json       # provenance on every clip
python3 render.py monetize render.json     # exposure under the policy
```

`short.py` runs all five and refuses to build if any fails.
