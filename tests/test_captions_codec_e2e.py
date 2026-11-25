"""Caption splitting and the scorer through the real Node codec, on exported-timeline fixtures.

The unit tests mock the codec; these run it. Skipped without Node and the
resolve-advanced packages, which CI installs on Linux and Windows.
"""
import contextlib
import importlib.util
import io
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from src import captions_server as cs
from src.utils import subtitle_live as live

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("compare_captions", ROOT / "scripts" / "compare_captions.py")
cc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cc)

NODE = shutil.which("node")
READY = bool(NODE) and live.subtitle_codec_gap() is None

# Two five-word Resolve auto-captions at 30 fps, as a native timeline export stores them.
BLOBS = [
    "00000002000000748128b52ffd206a5103000a5568007204204f682c72072074686174277372052073756368720220617205206375746578"
    "ec980d7894990d78ba990d78c6990d78d2990d800194990d8001ba990d8001c6990d8001d2990d8001de990da801b6cc0612110000002478"
    "9c6366606420040000b20005",
    "00000002000000738128b52ffd20694903000a5468007204206361747205206974277372022061720420646f6772072064756d6d792e78de"
    "990d78fa990d78ea9a0d78ec9a0d78809b0d8001fa990d8001ea9a0d8001ec9a0d8001809b0d8001949b0da801efcc06121100000024789c"
    "6366606420040000b20005",
]


def _generator(index, blob, name, start, duration):
    return (f'<Element><Sm2TiGenerator DbId="aaaaaaaa-0000-0000-0000-00000000000{index}"><FieldsBlob>{blob}</FieldsBlob>'
            f"<PrettyType>Subtitle</PrettyType><Name>{name}</Name><Start>{start}</Start><Duration>{duration}</Duration>"
            "<MarkersBA/><In/></Sm2TiGenerator></Element>")


def write_drt(path):
    items = (_generator(1, BLOBS[0], " Oh, that's such a cute", 108086, 57)
             + _generator(2, BLOBS[1], " cat it&apos;s a dog dummy.", 108143, 91))
    sequence = "33333333-3333-3333-3333-333333333333"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as drt:
        drt.writestr("project.xml", "<project/>")
        drt.writestr("MediaPool/Master/MpFolder.xml",
                     f'<Sm2Sequence DbId="{sequence}"><FrameRate>0000000000003e400000000000000000</FrameRate></Sm2Sequence>')
        drt.writestr("SeqContainer/22222222-2222-2222-2222-222222222222.xml",
                     "<Sm2SequenceContainer><SubtitleTrackVec><Element>"
                     f'<Sm2TiTrack DbId="11111111-1111-1111-1111-111111111111"><Sequence>{sequence}</Sequence>'
                     f"<Items>{items}</Items><FusionCompHolderItems/></Sm2TiTrack>"
                     "</Element></SubtitleTrackVec></Sm2SequenceContainer>")
    return path


@unittest.skipUnless(READY, "needs Node and resolve-advanced's packages (npm install in resolve-advanced)")
class CodecRoundTripTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.source = write_drt(self.dir / "source.drt")

    def codec(self, **request):
        return live._codec(NODE, request)

    def captions(self, path):
        return self.codec(operation="inspect_captions", source=str(path), track=1)["captions"]

    def split(self, max_words, name="split.drt"):
        """The write captions_split sends, applied by the real codec to a new export."""
        replace, add = [], []
        for caption in self.captions(self.source):
            cards = cs.split_caption(caption, max_words)
            if len(cards) > 1:
                replace.append({"id": caption["id"], **cards[0]})
                add.extend(cards[1:])
        out = self.dir / name
        self.codec(operation="write_captions", source=str(self.source), output=str(out), track=1,
                   replace=replace, add=add, delete=[])
        return out

    def test_split_cards_are_accepted_and_pass_the_check(self):
        for max_words in (1, 2, 3):
            out = self.split(max_words, f"split-{max_words}.drt")
            check = self.codec(operation="check_captions", source=str(out), track=1,
                               maxCharacters=24, minFramesPerWord=cs.MIN_FRAMES_PER_WORD)
            self.assertTrue(check["ok"], (max_words, check["issues"]))
            cards = self.captions(out)
            self.assertEqual((cards[0]["start"], cards[-1]["end"]), (108086, 108234), max_words)
            self.assertEqual(" ".join(c["text"] for c in cards), "Oh, that's such a cute cat it's a dog dummy.")
        self.assertEqual([c["text"] for c in self.captions(self.dir / "split-3.drt")],
                         ["Oh, that's such", "a cute", "cat it's a", "dog dummy."])

    def test_scorer_passes_a_match_and_fails_a_typo_and_a_changed_look(self):
        reference = self.dir / "reference.drt"
        self.codec(source=str(self.split(3)), output=str(reference), track=1,
                   preset="Word Highlight", inputs=cs.RECIPES["social_bold"])
        first = self.captions(reference)[0]
        typo = [{**w, "text": w["text"].replace("such", "sutch")} for w in first["words"]]
        retyped = self.dir / "typo.drt"
        self.codec(operation="write_captions", source=str(reference), output=str(retyped), track=1, add=[], delete=[],
                   replace=[{"id": first["id"], "text": " ".join(w["text"] for w in typo),
                             "start": first["start"], "end": first["end"], "words": typo}])
        candidate = self.dir / "candidate.drt"
        self.codec(source=str(retyped), output=str(candidate), track=1,
                   inputs={**cs.RECIPES["social_bold"], "allWordsOutlineThickness": 0.05})

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cc.main([str(reference), str(reference)]), 0)
            self.assertEqual(cc.main([str(reference), str(candidate)]), 1)
        report = cc.score(cc.load(str(reference), 1), cc.load(str(candidate), 1))
        self.assertEqual(report["failed"], ["word_error_rate", "style_match"])
        self.assertEqual(report["style_mismatches"], {"allWordsOutlineThickness": [0.03, 0.05]})


if __name__ == "__main__":
    unittest.main()
