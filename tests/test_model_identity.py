import unittest
from cartoon_sub.ai.model_selection import AIModelSelection
from cartoon_sub.app.settings import AISettings
class ModelIdentityTests(unittest.TestCase):
 def test_selection_identity_and_serialization(self):
  x=AIModelSelection('openrouter','google/gemini-2.5-flash'); self.assertEqual(AIModelSelection.from_value(x.to_dict()),x)
 def test_legacy_migrates(self):
  s=AISettings(default_ai_model='google/gemini-2.5-flash',translation_provider='openrouter',tab_model_overrides={'subtitle':'gemini-2.5-flash'}).validate()
  self.assertEqual(s.default_selection().provider,'openrouter'); self.assertEqual(s.tab_model_overrides['subtitle']['provider'],'openrouter')
 def test_legacy_native_tab_override_migrates_to_openrouter(self):
  s=AISettings(tab_model_overrides={'translate': {'provider':'gemini_native','model_id':'gemini-2.5-flash'}}).validate()
  self.assertEqual(s.tab_selection('translate'), AIModelSelection('openrouter','google/gemini-2.5-flash'))
 def test_official_id_never_uses_label(self):
  x=AIModelSelection('openrouter','google/gemini-2.5-flash'); self.assertNotIn('(official)',x.model_id)
 def test_new_defaults_are_openrouter_canonical_models(self):
  settings=AISettings()
  self.assertEqual(settings.default_selection().to_dict(), {'provider':'openrouter','model_id':'google/gemini-2.5-flash'})
  self.assertEqual(settings.default_selection('stt').to_dict(), {'provider':'openrouter','model_id':'google/gemini-3.5-transcribe'})
if __name__=='__main__': unittest.main()
