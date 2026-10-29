"""Offline content used when Groq can't be reached.

There's one hand-written case, plus scripted versions of every AI feature
(suspect replies, hints, endings). The scripted replies are built from the
case fields, so they also work as a stand-in for an AI-generated case if the
API drops out halfway through a game.
"""

OFFLINE_CASE = {
    "title": "The Halloway Observatory Blackout",
    "setting": "A hilltop observatory in 1956, on the stormy night a rare comet is due. "
    "At 10:40 pm the power died for twenty minutes.",
    "intro": "Rain is hammering the dome when you arrive, detective. The professor is dead, "
    "the phone lines are down, and nobody is leaving this hill until morning. "
    "Four people were in the building when the lights went out. One of them is lying to you.",
    "victim": {
        "name": "Professor Edmund Vale",
        "description": "the observatory's famous director, due to announce a major discovery on Friday",
    },
    "crime": "When the lights came back at 11:00 pm, Vale was found slumped over his desk in the dome, "
    "his tea half drunk. The local doctor says the tea was laced with a heavy dose of sleeping draught.",
    "locations": [
        {"name": "The Dome", "description": "The telescope room where Vale was found, charts scattered across his desk."},
        {"name": "Generator Shed", "description": "A cramped tin shed out back that powers the whole observatory."},
        {"name": "Kitchen", "description": "A narrow kitchen with a kettle still warm on the stove."},
        {"name": "Archive Room", "description": "Shelves of logbooks, ledgers and old star charts."},
    ],
    "suspects": [
        {
            "id": "s1",
            "name": "Dr. Miriam Kade",
            "role": "assistant astronomer",
            "personality": "precise and clipped, corrects people's facts mid-sentence",
            "relationship": "He was my mentor for eight years, and lately he took credit for my comet calculations.",
            "alibi": "I was in the Archive Room with a candle, checking star charts for the whole blackout.",
            "secret": "She had written to a rival university offering to publish the comet discovery under her own name.",
            "motive": "Vale was stealing credit for her work.",
            "is_culprit": False,
        },
        {
            "id": "s2",
            "name": "Tobias Wren",
            "role": "groundskeeper",
            "personality": "gruff, uses as few words as possible, says 'aye' a lot",
            "relationship": "Kept this place running for him for twenty years. He paid late, but he paid.",
            "alibi": "I was out in the storm checking the fence line when the power went.",
            "secret": "He has been quietly selling the observatory's old brass instruments.",
            "motive": "Vale had noticed instruments going missing and threatened to sack him.",
            "is_culprit": False,
        },
        {
            "id": "s3",
            "name": "Julian Marsh",
            "role": "wealthy patron",
            "personality": "smooth and charming, calls everyone 'old friend'",
            "relationship": "Edmund and I go back years. My family's money built this dome.",
            "alibi": "I was in the Kitchen making tea for everyone when the lights died, and I didn't leave until they came back.",
            "secret": "He poisoned Vale's tea and cut the generator line to steal ledger pages proving his foundation embezzled observatory funds.",
            "motive": "Vale was about to expose the Marsh foundation's fraud to the press.",
            "is_culprit": True,
        },
        {
            "id": "s4",
            "name": "Nora Pike",
            "role": "housekeeper and cook",
            "personality": "nervous, talks fast, apologises constantly",
            "relationship": "I cooked for the professor. He was kind to me, mostly.",
            "alibi": "I'd gone to bed early with a headache, I was asleep when it all happened.",
            "secret": "She slipped out to meet Tobias in the generator shed shortly before the blackout.",
            "motive": "Vale told her that morning she would be let go at the end of the month.",
            "is_culprit": False,
        },
    ],
    "clues": [
        {"id": "c1", "location": "The Dome", "text": "Beside Vale's teacup lies a monogrammed silver teaspoon engraved 'J.M.'", "points_to": "s3"},
        {"id": "c2", "location": "Generator Shed", "text": "The fuel line was sliced cleanly with a blade. In the mud is the print of an expensive leather dress shoe, not a work boot.", "points_to": "s3"},
        {"id": "c3", "location": "Generator Shed", "text": "A blue wool scarf hangs on a nail, still damp from the rain.", "points_to": "s4"},
        {"id": "c4", "location": "Kitchen", "text": "An empty bottle of sleeping draught is hidden behind the flour tin. The label reads 'J. Marsh, one at bedtime'.", "points_to": "s3"},
        {"id": "c5", "location": "Archive Room", "text": "A half-finished letter from Dr. Kade to a rival university, offering 'my' comet calculations for publication.", "points_to": "s1"},
        {"id": "c6", "location": "Archive Room", "text": "A foundation ledger with pages torn out. A note in Vale's handwriting says 'Marsh funds missing, tell the press Friday'.", "points_to": "s3"},
    ],
    "solution": "Julian Marsh did it. Vale had found that the Marsh foundation was stealing observatory money and planned to expose it on Friday, "
    "so Marsh slipped his own sleeping draught into the professor's tea and cut the generator line to take the ledger pages in the dark. "
    "His dress-shoe print in the shed, the bottle with his name on it and his monogrammed spoon all break his kitchen alibi.",
}

ALIBI_WORDS = ("where", "alibi", "night", "doing", "when", "time", "blackout", "lights", "power")
VICTIM_WORDS = ("know", "relationship", "victim", "professor", "feel", "like him", "like her", "get along")
MOTIVE_WORDS = ("why", "motive", "money", "angry", "hate", "fight", "argue", "kill", "secret", "hiding")


def _mentions_clue(question, clue):
    # crude, but enough to notice "what about the scarf?" style questions
    words = [w.strip(".,'\"!?").lower() for w in clue["text"].split()]
    keywords = {w for w in words if len(w) > 4}
    q = question.lower()
    return any(k in q for k in keywords)


def scripted_reply(case, suspect, question, found_clues):
    q = question.lower()
    guilty = suspect["is_culprit"]

    for clue in found_clues:
        if clue.get("points_to") == suspect["id"] and _mentions_clue(question, clue):
            if guilty:
                return "That... that could belong to anyone. You're grasping at straws, detective.", "nervous"
            return "I know how that looks. It isn't what you think, and it has nothing to do with the death.", "defensive"

    if any(w in q for w in ALIBI_WORDS):
        return suspect["alibi"], "defensive" if guilty else "calm"
    surname = case["victim"]["name"].split()[-1].lower()
    if surname in q or any(w in q for w in VICTIM_WORDS):
        return suspect["relationship"], "calm"
    if any(w in q for w in MOTIVE_WORDS):
        if guilty:
            return "Me? I had every reason to want this place to thrive. Look elsewhere.", "defensive"
        return "We had our disagreements, I won't pretend otherwise. But I didn't kill anyone.", "nervous"
    return "I've told you what I know. Ask me something specific, detective.", "calm"


def scripted_hint(case, found_ids, searched):
    culprit_id = next(s["id"] for s in case["suspects"] if s["is_culprit"])
    for clue in case["clues"]:
        if clue["points_to"] == culprit_id and clue["id"] not in found_ids and clue["location"] not in searched:
            return f"Something about {clue['location']} doesn't sit right with me. Have you looked there yet?"
    return "Line up each alibi against the evidence you've found. One of those stories can't be true."


def scripted_ending(case, accused, correct, culprit_clues_found):
    culprit = next(s for s in case["suspects"] if s["is_culprit"])
    if correct:
        scene = (
            f"You lay the evidence out on the table one piece at a time. {accused['name']} keeps smiling "
            "for a while, then stops. The room goes quiet, and the story finally cracks. "
            f"\"Fine,\" {accused['name']} says. \"You want to know why?\" The rest comes out slowly. "
            "By morning the storm has passed, and so has any doubt about what happened that night."
        )
        if culprit_clues_found >= 2:
            feedback = "Solid work. You had the evidence to back up the accusation."
        else:
            feedback = "Right answer, but you were leaning on a hunch more than proof."
    else:
        scene = (
            f"{accused['name']} protests, and within the hour the story checks out. The accusation falls apart. "
            f"In the confusion nobody notices {culprit['name']} quietly gathering their coat. "
            "By the time you realise your mistake, the real culprit has a head start."
        )
        feedback = "The evidence pointed somewhere else. Check which alibis the clues actually contradict."
    return scene, feedback
