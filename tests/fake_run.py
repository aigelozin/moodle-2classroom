import json, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from mbz_parser import parse_mbz
from convert import build_plan
import google_target as gt

calls = []

class Fake(gt.GoogleTarget):
    def __init__(self): self.publish_anyone = True; self.n = 0
    def _id(self, kind): self.n += 1; return f"{kind}{self.n}"
    def ensure_folder(self, name, parent, state, key):
        if not state.get(key): state.set(key, self._id("folder")); calls.append(("folder", name))
        return state.get(key)
    def upload_file(self, path, name, mime, folder):
        assert os.path.exists(path); calls.append(("upload", name, mime)); return self._id("file")
    def html_to_doc(self, html, name, folder):
        assert "@@PLUGINFILE@@" not in html and "<video" not in html.lower()
        calls.append(("doc", name)); return self._id("doc")
    def create_form(self, spec, folder):
        for r in spec.requests:
            if "createItem" in r:
                it = r["createItem"]["item"]
                assert "\n" not in it["title"], it["title"]
                q = it.get("questionItem", {}).get("question", {})
                g = q.get("grading")
                if g and "choiceQuestion" in q:
                    vals = [o["value"] for o in q["choiceQuestion"]["options"]]
                    assert len(set(vals)) == len(vals)
                    for a in g.get("correctAnswers", {}).get("answers", []):
                        assert a["value"] in vals, (a, vals)
                    assert "generalFeedback" not in g
        calls.append(("form", spec.title, len(spec.requests)))
        json.dump(spec.requests, open(os.path.join(os.environ.get("TMPDIR", "/tmp"), "m2c_form_requests.json"), "w"), ensure_ascii=False, indent=1)
        return {"id": self._id("form"), "url": "https://docs.google.com/forms/d/e/x/viewform"}
    def create_course(self, plan): calls.append(("course", plan.name)); return "C1"
    def create_topic(self, cid, name): calls.append(("topic", name)); return self._id("topic")
    def create_item(self, cid, tid, item, materials):
        assert len(materials) <= 20
        calls.append(("item", item.kind, item.title, item.draft, [list(m)[0] for m in materials]))
        return self._id("item")

state_file = os.path.join(os.environ.get("TMPDIR", "/tmp"), "m2c_state_test.json")
if os.path.exists(state_file): os.remove(state_file)
c = parse_mbz(sys.argv[1]); plan = build_plan(c)
gt.run_plan(plan, Fake(), gt.State(state_file), log=lambda *_: None)
for x in calls: print(x)
n1 = len(calls)
calls.clear()
gt.run_plan(plan, Fake(), gt.State(state_file), log=lambda *_: None)
print("повторный запуск, новых вызовов создания:", [x for x in calls if x[0] != "topic"])
