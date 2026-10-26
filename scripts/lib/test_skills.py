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


def preloaded_skills(agent_type: str) -> list[str]:
    """The skills in an agent's context from its first turn: its type's `skills:`, and for the orchestrator also the
    skills that the session-start hook adds. The test can't know which provider runs, so it adds them for every
    provider."""
    with mock.patch.object(usage, "ROOT", CHECKOUT):
        skills = usage.agent_skills(agent_type)
    if agent_type == "orchestrator":
        providers = [name.removeprefix("orchestrator-") for name in skill_names() if name.startswith("orchestrator-")]
        skills += [skill for provider in providers for skill in usage.orchestrator_skills(provider)]
    return list(dict.fromkeys(skills))


def without_front_matter(text: str) -> str:
    return text.split("---", 2)[2]


def section(text: str, heading: str) -> str:
    return text.split(f"## {heading}\n")[1].split("\n## ")[0]


def names_skill(text: str, skill: str) -> bool:
    """Whether the text names the skill as `planning`, "the planning skill" or a path to its folder."""
    return bool(re.search(rf"`{skill}`|\b{skill}`? skill\b|skills/{skill}(?![\w-])", text))


class PreloadTest(unittest.TestCase):
    def test_each_agent_type_preloads_every_skill_that_its_preloaded_skills_or_agent_file_name(self):
        names = skill_names()
        not_preloaded_by: dict[tuple[str, str], list[str]] = {}
        for agent_file in AGENT_FILES:
            preloaded = preloaded_skills(agent_file.stem)
            texts = {
                f".agents/skills/{skill}/SKILL.md": (SKILLS / skill / "SKILL.md").read_text()
                for skill in preloaded
                if skill in names
            }
            texts[f".claude/agents/{agent_file.name}"] = without_front_matter(agent_file.read_text())
            for path, text in texts.items():
                for skill in names:
                    if skill not in preloaded and names_skill(text, skill):
                        not_preloaded_by.setdefault((path, skill), []).append(agent_file.name)
        problems = [
            f"`{path}` names the `{skill}` skill, which isn't in the `skills:` of "
            f"{', '.join(f'`.claude/agents/{name}`' for name in agent_file_names)}. Add it there, or take the mention "
            f"out of `{path}`."
            for (path, skill), agent_file_names in not_preloaded_by.items()
        ]
        self.assertEqual(problems, [])

    def test_each_preloaded_skill_exists(self):
        names = skill_names()
        problems = [
            f"`.claude/agents/{agent_file.name}` preloads `{skill}`, but `.agents/skills/{skill}/SKILL.md` doesn't "
            "exist. Correct the name, or add the skill."
            for agent_file in AGENT_FILES
            for skill in preloaded_skills(agent_file.stem)
            if skill not in names
        ]
        self.assertEqual(problems, [])

    def test_agents_md_lists_each_skill_that_no_agent_type_preloads(self):
        preloaded = {skill for agent_file in AGENT_FILES for skill in preloaded_skills(agent_file.stem)}
        agents_md = (CHECKOUT / "AGENTS.md").read_text()
        self.assertIn("## Read the skills\n", agents_md, 'This test reads the list in AGENTS.md\'s "Read the skills".')
        listed = set(re.findall(r"^- `([^`]+)`", section(agents_md, "Read the skills"), re.MULTILINE))
        problems = [
            f'No agent type preloads `{skill}`, and the section "Read the skills" in AGENTS.md doesn\'t list it, so no '
            "agent finds it. Add it to that section, or to the `skills:` of the agent types that need it."
            for skill in skill_names()
            if skill not in preloaded and skill not in listed
        ]
        self.assertEqual(problems, [])
