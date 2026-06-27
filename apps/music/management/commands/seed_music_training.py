"""
music/management/commands/seed_music_training.py

Creates an out-of-the-box Music Directing & Worship Team Training course
for a church, pre-populated with structured modules covering:

  1. Foundations of Worship Ministry
  2. Music Theory Essentials for the Worship Team
  3. Instrument Roles & Stage Etiquette
  4. Setlist Planning & Song Arrangement
  5. Leading Rehearsals Effectively
  6. Sound Engineering Basics for Worship
  7. Chord Charts, Nashville Number System & Transposition
  8. Worship Dynamics: Keys, Tempo & Flow
  9. Lyric Projection & Media Coordination
  10. Music Directing: Leadership & Communication

Usage
-----
# Create for ALL churches (idempotent — skips if course already exists)
python manage.py seed_music_training

# Create for one specific church
python manage.py seed_music_training --church-id 3

# Re-create from scratch (deletes existing modules and rebuilds)
python manage.py seed_music_training --force

# Link to a specific music unit slug (so trainees in that unit get enrolled)
python manage.py seed_music_training --unit-slug choir

# Set course type: unit_training (default) or workforce
python manage.py seed_music_training --course-type workforce
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction


# ── Module definitions ────────────────────────────────────────────────────────
# Each entry: (order, title, content_type, estimated_minutes, description, content,
#              external_url, requires_attachment, is_required)
MUSIC_TRAINING_MODULES = [
    (
        1,
        "Foundations of Worship Ministry",
        "text",
        25,
        "Understanding the heart and purpose of worship in the local church.",
        """\
Worship is more than music — it is a lifestyle of surrender and service to God \
that happens to express itself through song, prayer, and proclamation within the \
gathered church community.

**Why we serve on the worship team**
The worship team exists to create a space where the congregation can encounter God. \
Our role is facilitation, not performance. Every chord, lyric, and dynamic choice \
should point people upward, not inward.

**Core values we hold**
1. Excellence — preparation honours God and blesses the congregation.
2. Humility — we decrease so He increases (John 3:30).
3. Unity — a divided team cannot lead a unified congregation.
4. Consistency — faithfulness in the hidden rehearsal room shows up on the platform.
5. Prayer — every service preparation begins with intercession, not sound-check.

**The worship leader vs the music director**
The worship leader carries the spiritual vision for the service. \
The music director is responsible for musical execution — arranging, \
directing the band, managing the setlist flow, and ensuring rehearsals run efficiently. \
In many churches one person holds both roles; understanding both functions will \
make you a more complete team member regardless of your specific role.

**Reflection question**
Write a brief personal statement of why you are part of the worship team, \
and what you believe God wants to do through your musical gift in this season.
""",
        "",
        True,
        True,
    ),
    (
        2,
        "Music Theory Essentials for the Worship Team",
        "text",
        40,
        "Practical music theory you actually use — keys, chords, intervals, and the number system.",
        """\
You do not need a music degree to serve on the worship team. \
But a working knowledge of a few core concepts will make you a far more \
adaptable and reliable musician.

**Keys and scales**
Every song lives in a key — a home base of seven notes from which all the \
chords and melodies are drawn. The most common worship keys are G, A, Bb, C, D, and E. \
Practise playing a major scale in each of these keys until it is automatic.

**The I-IV-V-vi pattern**
The vast majority of contemporary worship songs use some arrangement of the \
first (I), fourth (IV), fifth (V), and sixth minor (vi) chords of the key. \
In the key of G: G–C–D–Em. In A: A–D–E–F#m. \
Recognising this pattern by ear lets you pick up new songs rapidly.

**Intervals and transposition**
An interval is the distance between two notes. \
Transposing a song means moving it up or down by a fixed number of semitones. \
If a song is in E and the vocalist needs it in D, you drop everything by two semitones. \
The Nashville Number System (covered in Module 7) makes transposition effortless.

**Rhythm and time signatures**
4/4 time — four beats per bar — covers perhaps 90 % of worship music. \
3/4 (waltz) appears in hymns and some contemporary ballads. \
6/8 and 12/8 give a "rolling" feel common in Afro-gospel and contemporary worship \
(e.g. "Reckless Love", most Nathaniel Bassey material).

**Assignment**
Identify the key and the I-IV-V-vi chords for three songs in your church's setlist. \
Write them down and submit below.
""",
        "",
        True,
        True,
    ),
    (
        3,
        "Instrument Roles & Stage Etiquette",
        "text",
        20,
        "How every instrument serves the song — and how to act on and off the platform.",
        """\
**The rhythm section (drums + bass)**
The drums and bass guitar form the rhythmic and harmonic foundation. \
Their relationship — specifically the lock between the kick drum and the bass note — \
is what makes a band feel tight. If the rhythm section is loose, no amount of \
guitar or keys work will save the mix.

**Harmonic layers (keys, guitar, additional pads)**
- **Piano/Keys**: Can cover full chords with voicings that add texture. \
  In a full band, keys should often voice chords in a way that complements, \
  not duplicates, the guitar's range.
- **Guitar**: Electric guitar typically handles rhythmic drive and melodic fills; \
  acoustic guitar provides rhythmic strumming texture.
- **Synth pads**: Held chords that create atmosphere and smoothe transitions. \
  Often the most important "invisible" element — when absent, worship can feel harsh.

**Melodic leads (lead guitar, keys solo voice, horns)**
These add melodic interest between vocal phrases. Use tastefully — \
empty space is often more powerful than fills.

**Vocals**
Backing vocalists harmonise and support the lead. \
They should not compete with the lead melodically, especially during the lead singer's phrases.

**Stage etiquette**
- Arrive at least 30 minutes before rehearsal; do not wait for sound-check to tune.
- No mobile phones on the platform during service.
- If you make a mistake: keep going, do not react visibly on stage.
- Communicate with the music director through eye contact or agreed hand signals during service.
- After service: pack down your equipment, coil cables properly, help others.
""",
        "",
        False,
        True,
    ),
    (
        4,
        "Setlist Planning & Song Arrangement",
        "text",
        30,
        "How to build a setlist that takes a congregation on a journey.",
        """\
A setlist is not a random collection of songs — it is a curated journey with \
an intentional beginning, middle, and end.

**The arc of a worship set**
A classic worship arc moves through four phases:
1. **Invitation / Celebration** — high-energy, familiar songs that open hearts and focus attention.
2. **Exaltation** — songs of praise and declaration, often louder and more corporate.
3. **Intimacy** — slower, personal songs of surrender and encounter.
4. **Response / Commission** — a closing declaration or commissioning song that \
   sends the congregation into the message or into their week.

Not every set needs all four phases, and the order can vary based on the service flow, \
the message topic, and what the Spirit is doing. But having an arc prevents a set from \
feeling like a random playlist.

**Song selection criteria**
- Theological soundness — every lyric we sing is a declaration of belief.
- Congregational singability — if the congregation cannot sing it, it is a performance, not worship.
- Key compatibility — adjacent songs should not clash in key; aim for smooth transitions \
  (same key, a fourth up, a fifth up, or relative major/minor).
- Tempo flow — avoid whiplash; if you must shift tempo, use a transition or spoken word.
- Repetoire balance — new songs, classic hymns, and current congregational favourites in balance.

**Practical setlist building in ChurchForce**
Use the Setlists section of the Music module to create and finalise your setlist \
before the service. Linking a setlist to the service event auto-generates lyric slides \
via the media module once you finalise it.

**Assignment**
Plan a five-song setlist for a Sunday morning service with the theme "God is our refuge." \
Include the key, tempo, and a one-sentence rationale for each song selection.
""",
        "",
        True,
        True,
    ),
    (
        5,
        "Leading Rehearsals Effectively",
        "text",
        25,
        "How to run a focused, productive rehearsal that builds team unity.",
        """\
A well-run rehearsal is the difference between a confident, cohesive team \
and a group of musicians nervously hoping it comes together on Sunday.

**Before rehearsal**
- Distribute the setlist, chord charts, and any key changes at least 48 hours in advance.
- Assign learning responsibilities — who is playing what, in which key.
- Upload or link reference recordings for any new songs.
- Prepare your session notes: which sections need focus, any specific arrangements.

**The rehearsal structure**
1. **Prayer** (5 min) — set the spiritual tone.
2. **Full run-through** (20–30 min) — play the set from top to bottom to identify problems.
3. **Breakdown** (30–45 min) — work the problem sections: beginnings, endings, transitions, key changes.
4. **Full run-through again** (15–20 min) — rebuild confidence after the breakdown work.
5. **Close in prayer** — dedicate the service to God together.

**Direction techniques**
- Use clear verbal cues: "From the chorus," "Let's take it down on the bridge," \
  "Hold at bar 8."
- Use hand signals for dynamics: open palm = sustain/pad; fist = cut; \
  fingers up = crescendo; fingers down = decrescendo.
- Praise specifically: "That transition into the bridge was clean — let's lock that."
- Correct gently but specifically: "Keys, can you drop the left hand on the verse? \
  Too much low-end with the bass."
- Protect team culture — never shame a musician in front of the team.

**Time discipline**
Start on time. End on time. Runover signals poor preparation to your team.

**Assignment**
Write a rehearsal plan for a three-song set including a new song your team has not \
played before. Include timeline, specific sections to break down, and a brief \
arrangement note for each song.
""",
        "",
        True,
        True,
    ),
    (
        6,
        "Sound Engineering Basics for Worship",
        "text",
        30,
        "What every worship team member needs to know about the sound desk.",
        """\
You do not have to be the sound engineer to benefit from understanding the basics. \
A worship team that understands sound fundamentals communicates better with the \
engineer, results in a better mix, and a better congregational experience.

**Signal flow**
Sound travels: instrument/microphone → direct box or mic preamp → \
channel strip on the mixing console → main mix → amplifier → speakers. \
Understanding this chain helps you diagnose problems quickly.

**The three zones of the mix**
1. **Front of House (FOH)** — what the congregation hears. Managed by the FOH engineer.
2. **Stage monitors / IEMs** — what the musicians hear on stage. Critical for performance confidence.
3. **Recording/streaming mix** — often a separate submix for broadcast.

**Gain staging**
The gain on each channel should be set so that normal playing peaks \
at around -18 dBFS on a digital console, leaving headroom for dynamics. \
Never clip (go into the red). Clipping destroys clarity and can damage speakers.

**EQ basics**
- Low-cut/high-pass filter: remove rumble below 80–100 Hz from most instruments \
  (especially acoustic guitar and vocals). This clears the mix enormously.
- Mid scooping: a small cut around 300–500 Hz on guitar and keys reduces muddiness.
- Presence boost: a small lift around 2–4 kHz adds clarity to vocals.

**The monitor mix and IEM use**
Each musician should have a customised monitor mix. \
IEMs (in-ear monitors) allow much lower stage volume, \
which dramatically improves FOH clarity. \
Work with your engineer to build a monitor mix that lets you hear \
yourself, the click, and the lead vocal clearly above everything else.

**Communicating with the engineer during service**
- Agree on hand signals before the service.
- Point to your ear and up/down for monitor adjustments.
- Never shout across the stage to the sound desk during service.

**Assignment**
Write a brief note to your sound engineer for your next service: \
what your ideal monitor mix includes, any known frequency problems \
with your instrument, and a specific question about the FOH mix you want resolved.
""",
        "",
        False,
        True,
    ),
    (
        7,
        "Chord Charts, Nashville Number System & Transposition",
        "text",
        35,
        "Master the tools that make every musician on your team instantly adaptable.",
        """\
The Nashville Number System (NNS) is the most powerful shorthand \
a worship musician can learn. It replaces note names with numbers \
relative to the key — so a chart written in numbers works in ANY key \
without rewriting.

**How it works**
In any major key, assign the scale degrees numbers 1 through 7:
- Key of C: 1=C, 2=D, 3=E, 4=F, 5=G, 6=Am, 7=Bdim
- Key of G: 1=G, 2=Am, 3=Bm, 4=C, 5=D, 6=Em, 7=F#dim

A verse that reads "1 – 4 – 5 – 6m" is: G–C–D–Em in G, or C–F–G–Am in C. \
The musician transposing on the fly simply maps the numbers to the new key.

**Reading a Nashville chart**
A typical NNS chart shows:
- Bars with chord numbers inside (e.g. | 1 | 4 | 5 5 | 6m |)
- Section labels (V = Verse, Ch = Chorus, Br = Bridge, Tag)
- Diamonds (◇) for sustained chords, dots for staccato
- Split bars for two chords per bar: | 1 / 4 |
- Flat and sharp modifiers: b3, #4

**Traditional chord charts**
Many teams use letter-name chord charts (G – C – Em – D). \
These are faster to write but require rewriting for each key. \
ChurchForce's chord chart tool supports both formats and stores multiple \
charts per song (e.g. Band Default in G, Vocalist Chart in A, Acoustic in D).

**Transposition workflow**
1. Identify the original key of the song.
2. Count the semitone distance to the new key (e.g. G to A = +2 semitones).
3. Shift every chord up or down by that interval.
4. Check for any open-string voicings that become awkward; suggest a capo if needed.

**When to use a capo**
A capo allows a guitarist to use open-chord shapes in a different sounding key. \
Example: Capo 2 in the "key of G" shapes sounds in A. \
This is particularly useful in Nigerian worship music where keys like Eb and Bb \
are common (vocally comfortable) but awkward for guitarists without a capo.

**Assignment**
Take any three songs from your church's current setlist. \
Write the chord progression in Nashville Numbers. \
Then transpose each song to a different key and write the letter-name chords \
for the new key. Submit your work below.
""",
        "",
        True,
        True,
    ),
    (
        8,
        "Worship Dynamics: Keys, Tempo & Flow",
        "text",
        20,
        "The art of musical pacing — making the congregation feel the Spirit, not just hear the song.",
        """\
Dynamics in worship are the intentional variation in volume, intensity, \
tempo, and instrumentation that gives a set emotional and spiritual shape. \
A worship set that stays at the same volume and energy throughout is exhausting; \
one that has no quiet moments has no room for the congregation to respond.

**Volume dynamics**
- Use the full range: whisper-quiet pads under a prayer moment, \
  then build to full-band celebration.
- "The valley before the mountain": a brief quiet section before a big \
  climactic chorus makes the climax feel earned.
- "Cut to one instrument": stripping to a single piano or acoustic guitar \
  for the bridge creates powerful contrast.

**Tempo dynamics**
- Most worship sets benefit from at least one clear tempo shift.
- Slow-fast: beginning the set uptempo then settling into a slower song creates energy \
  followed by depth.
- Fast-slow-fast: the classic; celebration → intimacy → recommissioning.
- Avoid abrupt tempo changes without a musical transition (a solo bar, \
  a spoken word bridge, or a moment of silence works well).

**Key flow and modulation**
- Songs in adjacent keys transition smoothly (e.g. G to D, or G to C).
- A half-step modulation mid-song (e.g. from D to Eb mid-chorus) \
  creates instant lift and intensity — use sparingly.
- The music director should have the setlist key chart mapped before \
  rehearsal so transitions are planned, not improvised.

**Instrumental moments and soaking**
- A "soaking" moment is an instrumental interlude under prayer or a spoken word. \
  The band holds a simple loop (often I–IV–I or I–vi–IV–V) at low volume \
  while the worship leader or pastor speaks.
- Practise these moments in rehearsal — an unrehearsed soak can collapse \
  awkwardly or become an unintended song interlude.

**Directing dynamics in the moment**
Your job as music director is to read the room AND listen to the Spirit. \
Sometimes the planned setlist should be abandoned if something is clearly moving. \
Know your musicians well enough to communicate changes on the fly \
through your agreed hand signals and eye contact.
""",
        "",
        False,
        True,
    ),
    (
        9,
        "Lyric Projection & Media Coordination",
        "text",
        20,
        "Syncing the music team with the media team for a seamless service experience.",
        """\
The projection of lyrics is the congregation's connection to the words they are singing. \
A mistimed slide, a wrong key, or a missing song leaves the congregation \
guessing — and pulls attention away from worship.

**How lyric projection works in ChurchForce**
When a setlist is finalised in the Music module, ChurchForce \
auto-generates a ServicePresentation in the Media module, \
with one slide per LyricsSection (verse, chorus, bridge, etc.) in order. \
The media operator advances slides in real time during service.

**What the music director must provide**
- Finalised setlist (not a draft) at least 24 hours before service — \
  so the media team can review and prepare.
- Correct section order for each song (the LyricsSection order in ChurchForce \
  determines slide order — verify it in the Track Detail view).
- Notification of any key changes, repeats, or spontaneous sections \
  ("we'll probably repeat the chorus an extra time after the bridge").

**Communicating changes on the day**
- Do not change the setlist order in the room without telling the media operator.
- Agree on a visual cue for "go back to previous slide" and "skip ahead."
- If you extend a section spontaneously, the operator needs a heads-up — \
  eye contact or a hand signal pointing backward/forward works well.

**Slide design best practices** (for music directors who also oversee media)
- Maximum 2–4 lines per slide, never more than 8 words per line.
- High-contrast text on background: white text on dark image or dark gradient.
- Consistent, readable font (avoid thin/script fonts for projection).
- Never cut a sentence across two slides if it can be avoided.

**Assignment**
Review the lyric sections for one song in ChurchForce's track library. \
Are the sections in the right order? Are any sections missing or mislabelled? \
Write a short report of what you found and any corrections you made.
""",
        "",
        False,
        True,
    ),
    (
        10,
        "Music Directing: Leadership & Communication",
        "assessment",
        45,
        "Final assessment — demonstrating readiness to lead the worship team.",
        """\
This final module is a written assessment of your readiness to serve \
as a music director or senior worship team member.

**Section A — Situational Responses** (answer all three)

1. It is 20 minutes before service. The lead vocalist has just told you they have \
   lost their voice and cannot lead today. You have a backup vocalist but they \
   have only sung this set once. Describe what you do in the next 20 minutes, \
   step by step.

2. Halfway through Sunday's worship set, the drummer's kick drum pedal breaks. \
   Describe how you direct the team through this situation while keeping the \
   congregation engaged.

3. The lead pastor approaches you after service and says the worship was "too loud \
   and too long." How do you receive this feedback, what questions do you ask, \
   and what changes — if any — do you implement for the following Sunday?

**Section B — Musical Plan**

Design a full worship set for a special occasion service (choose: Easter, \
Christmas, a church anniversary, or a missions Sunday). Include:
- Five songs with artist, key, tempo (BPM), and a one-sentence arrangement note
- A proposed dynamic arc (mark where the set rises, peaks, and comes down)
- Any spontaneous or soaking sections you would plan for
- A note to the sound engineer about the general feel of the mix you want

**Section C — Personal Reflection**

Write a paragraph on what you believe is the greatest strength you bring \
to the worship team, and what area you are actively working to develop. \
How does this training course change or affirm your approach?

Submit all three sections as your response below. \
Attach any supporting chord charts, setlist documents, or diagrams as a file.
""",
        "",
        True,
        True,
    ),
]


class Command(BaseCommand):
    help = (
        "Seed an out-of-the-box Music Directing & Worship Team Training "
        "course for one or all churches."
    )

    COURSE_TITLE = "Music Directing & Worship Team Training"
    COURSE_SLUG_BASE = "music-directing-training"

    def add_arguments(self, parser):
        parser.add_argument(
            "--church-id",
            type=int,
            default=None,
            help="Seed for one church only (by DB id). Default: all active churches.",
        )
        parser.add_argument(
            "--unit-slug",
            type=str,
            default=None,
            help="Slug of a music unit to link the course to (unit_training). "
                 "If omitted the course is church-wide (workforce type).",
        )
        parser.add_argument(
            "--course-type",
            type=str,
            default="unit_training",
            choices=["unit_training", "workforce"],
            help="Course type: unit_training (default) or workforce.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            default=False,
            help="Delete existing modules and rebuild from scratch.",
        )

    def handle(self, *args, **options):
        from tenants.models import Church
        from lms.models import LMSCourse, LMSModule

        church_id = options["church_id"]
        unit_slug = options["unit_slug"]
        course_type = options["course_type"]
        force = options["force"]

        churches = (
            Church.objects.filter(id=church_id, is_active=True)
            if church_id
            else Church.objects.filter(is_active=True)
        )

        if not churches.exists():
            raise CommandError(
                f"No active church found{f' with id={church_id}' if church_id else ''}."
            )

        for church in churches:
            self._seed_for_church(church, unit_slug, course_type, force)

    @transaction.atomic
    def _seed_for_church(self, church, unit_slug, course_type, force):
        from lms.models import LMSCourse, LMSModule
        from units.models import ChurchUnit

        unit = None
        if unit_slug:
            unit = ChurchUnit.raw_objects.filter(
                church=church, slug=unit_slug, is_active=True
            ).first()
            if not unit:
                self.stdout.write(
                    self.style.WARNING(
                        f"  [{church.name}] Unit '{unit_slug}' not found — "
                        "creating as church-wide workforce course."
                    )
                )

        # ── Find or create the course ────────────────────────────────────────
        course = LMSCourse.raw_objects.filter(
            church=church, slug__startswith=self.COURSE_SLUG_BASE
        ).first()

        if course and not force:
            self.stdout.write(
                self.style.WARNING(
                    f"  [{church.name}] Course already exists: '{course.title}' "
                    f"(slug={course.slug}). Use --force to rebuild. Skipping."
                )
            )
            return

        if course and force:
            self.stdout.write(
                f"  [{church.name}] --force: deleting {course.modules.count()} "
                "existing modules…"
            )
            course.modules.all().delete()
        else:
            course = LMSCourse(church=church)

        course.title = self.COURSE_TITLE
        course.description = (
            "A comprehensive training programme for worship team members and music directors. "
            "Covers worship theology, music theory, instrument roles, setlist planning, "
            "rehearsal technique, sound engineering, chord charts, the Nashville Number System, "
            "dynamics, media coordination, and leadership. "
            "Complete all 10 modules and submit the final assessment for certification."
        )
        course.course_type = course_type
        course.unit = unit
        course.delivery_mode = "hybrid"
        course.passing_score = 70
        course.allow_retake = True
        course.max_retakes = 2
        course.strict_sequence = True
        course.certificate_template = (
            "This is to certify that {name} has successfully completed the "
            "{course} at {church}. Awarded on {date}."
        )
        course.is_active = True
        course.save()  # triggers slug auto-generation

        # ── Create modules ───────────────────────────────────────────────────
        created = 0
        for (
            order, title, content_type, estimated_minutes,
            description, content, external_url,
            requires_attachment, is_required,
        ) in MUSIC_TRAINING_MODULES:
            LMSModule.raw_objects.create(
                church=church,
                course=course,
                order=order,
                title=title,
                content_type=content_type,
                estimated_minutes=estimated_minutes,
                description=description,
                content=content.strip(),
                external_url=external_url,
                is_required=is_required,
                requires_attachment=requires_attachment,
                is_active=True,
            )
            created += 1
            self.stdout.write(f"    [{church.name}] ✓ Module {order}: {title}")

        self.stdout.write(
            self.style.SUCCESS(
                f"  [{church.name}] Created course '{course.title}' "
                f"(slug={course.slug}, type={course_type}) with {created} modules."
            )
        )
