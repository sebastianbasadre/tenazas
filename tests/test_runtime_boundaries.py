import unittest

from workspace.runtime import skill_registry


class RuntimeBoundaryTests(unittest.TestCase):
    def test_workspace_runtime_is_importable_package(self):
        import workspace.runtime

        self.assertTrue(hasattr(workspace.runtime, "__doc__"))

    def test_skill_registry_exposes_metadata(self):
        names = skill_registry.list_skill_names()
        self.assertIn("skill:weather", names)
        self.assertFalse(skill_registry.is_destructive("skill:weather"))
        self.assertIn("description", skill_registry.get_skill_definition("skill:weather"))


if __name__ == "__main__":
    unittest.main()
