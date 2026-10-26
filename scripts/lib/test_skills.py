import re
import unittest
from pathlib import Path
from unittest import mock

import usage

# usage.ROOT is the main checkout, but a branch's own agent files and skills are in the checkout that holds this test.
CHECKOUT = Path(__file__).resolve().parents[2]
SKILLS = CHECKOUT / ".agents" / "skills"
AGENT_FILES = sorted((CHECKOUT / ".claude" / "agents").glob("*.md"))


def skill_names() -> list[str]:
    return sorted(path.name for path in SKILLS.iterdir() if (path / "SKILL.md").is_file())


def given_skills(agent_type: str) -> list[str]:
    """The skills that an agent of the type starts with. The session-start hook adds the orchestrator's own skill and
    the one for its provider, so the orchestrator counts the ones for every provider."""
    with mock.patch.object(usage, "ROOT", CHECKOUT):
        skills = usage.agent_skills(agent_type)
    if agent_type == "orchestrator":
        providers = [name.removeprefix("orchestrator-") for name in skill_names() if name.startswith("orchestrator-")]
        skills += [skill for provider in providers for skill in usage.orchestrator_skills(provider)]
    return skills


class PreloadTest(unittest.TestCase):
    def test_each_agent_type_preloads_every_skill_that_its_texts_mention(self):
        names = skill_names()
        readers: dict[tuple[str, str], list[str]] = {}
        for agent_file in AGENT_FILES:
            given = given_skills(agent_file.stem)
            texts = {f".agents/skills/{skill}/SKILL.md": (SKILLS / skill / "SKILL.md").read_text() for skill in given}
            texts[f".claude/agents/{agent_file.name}"] = agent_file.read_text().split("---", 2)[2]
            for path, text in texts.items():
                for mentioned in names:
                    if f"`{mentioned}`" in text and mentioned not in given:
                        readers.setdefault((path, mentioned), []).append(f"`.claude/agents/{agent_file.name}`")
        problems = []
        for (path, skill), agents in readers.items():
            preload = "doesn't preload" if len(agents) == 1 else "don't preload"
            problems.append(
                f"`{path}` mentions the `{skill}` skill, but {', '.join(agents)} {preload} it. Add `{skill}` to the "
                f"`skills:` of each, or take the mention out of `{path}`."
            )
        self.assertEqual(problems, [])

    def test_each_preloaded_skill_exists(self):
        names = skill_names()
        problems = [
            f"`.claude/agents/{agent_file.name}` preloads `{skill}`, which isn't a folder in `.agents/skills/`."
            for agent_file in AGENT_FILES
            for skill in given_skills(agent_file.stem)
            if skill not in names
        ]
        self.assertEqual(problems, [])

    def test_agents_md_lists_each_skill_that_no_agent_type_preloads(self):
        preloaded = {skill for agent_file in AGENT_FILES for skill in given_skills(agent_file.stem)}
        section = (CHECKOUT / "AGENTS.md").read_text().split("## Read the skills")[1].split("\n## ")[0]
        listed = set(re.findall(r"^- `([^`]+)`", section, re.MULTILINE))
        problems = [
            f"No agent type preloads `{skill}`, and AGENTS.md's \"Read the skills\" doesn't list it, so no agent "
            "finds it. Add it to that list, or to the `skills:` of the agent types that need it."
            for skill in skill_names()
            if skill not in preloaded and skill not in listed
        ]
        self.assertEqual(problems, [])
