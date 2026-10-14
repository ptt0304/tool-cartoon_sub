import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from cartoon_sub.ai.model_resolver import AIModelResolution, AIModelResolver
from cartoon_sub.app.settings import AISettings
from cartoon_sub.app.controller import Controller
from cartoon_sub.project.project_manager import ProjectManager
from cartoon_sub.subtitle.models import Project, Segment
from cartoon_sub.translation.conversation_media import ConversationEvidence
from cartoon_sub.translation.pipeline import TranslationPipeline

class FakeClient:
    created_with=[]
    def __init__(self, credential): self.credential=credential; type(self).created_with.append(credential)
    def close(self): pass
    def generate_json(self,*_args,**_kwargs): return {"translations":[{"id":1,"vi":"Xin chào.","confidence":.9,"review_note":"","meaning_preservation":"high","compressed":False}],"continuity_updates":[],"uncertainties":[]}
class FakeMedia:
    def build(self,*_args): return ConversationEvidence("none",(0,1),[],"none",{"mode":"none"})
class Store:
    def __init__(self): self.settings=AISettings(default_ai_model="google/gemini-2.5-flash",translation_chunk_size=30,retry_count=0,tab_model_overrides={"translate":{"provider":"openrouter","model_id":"google/gemini-2.5-flash"}}).validate()
    def load(self): return self.settings
    def openrouter_catalog_cache(self): return {"models":[{"id":"google/gemini-2.5-flash","architecture":{"input_modalities":["text"],"output_modalities":["text"]}}]}
    def openrouter_key_pool(self): return "openrouter-key"
class TranslationProviderRoutingTests(unittest.TestCase):
    def _project(self): return Project("routing","video.mp4",segments=[Segment(1,0,1,"你好")])
    def test_openrouter_selection_uses_resolver_and_factory(self):
        store=Store(); pipeline=TranslationPipeline(store,media_builder=FakeMedia()); resolution=AIModelResolution("google/gemini-2.5-flash","TAB_OVERRIDE","TRANSLATION","translate",("text",))
        FakeClient.created_with=[]
        with tempfile.TemporaryDirectory() as root, patch("cartoon_sub.translation.pipeline.text_client_factory",return_value=FakeClient) as factory, patch("cartoon_sub.ai.model_resolver.AIModelResolver.resolve",return_value=resolution) as resolver:
            result,_=pipeline.run(self._project(),Path(root))
        self.assertEqual(result.segments[0].vi_subtitle,"Xin chào."); resolver.assert_called_once(); factory.assert_called_once_with("openrouter"); self.assertEqual(FakeClient.created_with,["openrouter-key"])
    def test_legacy_provider_identity_migrates_before_resolver(self):
        store=Store(); store.settings=AISettings(tab_model_overrides={"translate":{"provider":"gemini_native","model_id":"gemini-2.5-flash"}}).validate()
        self.assertEqual(AIModelResolver(store).resolve("TRANSLATION","translate",("text",)).model_id,"google/gemini-2.5-flash")
    def test_controller_always_uses_openrouter_resolver(self):
        store=Store(); controller=Controller.__new__(Controller); controller.settings_store=store; controller.ai_model_resolver=Mock(); controller.ai_model_resolver.resolve.return_value=AIModelResolution("google/gemini-2.5-flash","TAB_OVERRIDE","TRANSLATION","translate",("text",)); controller.translation_pipeline=Mock(); controller.project,controller.directory=object(),"project"
        controller.translate(); controller.ai_model_resolver.resolve.assert_called_once(); self.assertEqual(controller.translation_pipeline.run.call_args.kwargs["model"],"google/gemini-2.5-flash")

    def test_dubbing_features_share_the_effective_translate_selection(self):
        store = Store()
        resolver = AIModelResolver(store)
        expected = "google/gemini-2.5-flash"
        self.assertEqual(resolver.resolve("TRANSLATION", "translate", ("text",)).model_id, expected)
        self.assertEqual(resolver.resolve("DUBBING_OPTIMIZE", "translate", ("text",)).model_id, expected)
        self.assertEqual(resolver.resolve("DUBBING_DURATION_REWRITE", "translate", ("text",)).model_id, expected)

        store.settings.tab_model_overrides["dubbing_optimize"] = {
            "provider": "openrouter", "model_id": "different/legacy-dubbing-model",
        }
        self.assertEqual(resolver.resolve("DUBBING_OPTIMIZE", "translate", ("text",)).model_id, expected)
        self.assertEqual(resolver.resolve("DUBBING_DURATION_REWRITE", "translate", ("text",)).model_id, expected)

    def test_changing_translate_override_changes_all_dubbing_resolutions(self):
        store = Store()
        store.settings.tab_model_overrides["translate"] = {
            "provider": "openrouter", "model_id": "google/gemini-2.5-flash-lite",
        }
        store.openrouter_catalog_cache = lambda: {"models": [
            {"id": "google/gemini-2.5-flash-lite",
             "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]}},
        ]}
        resolver = AIModelResolver(store)
        expected = "google/gemini-2.5-flash-lite"
        self.assertEqual(resolver.resolve("TRANSLATION", "translate", ("text",)).model_id, expected)
        self.assertEqual(resolver.resolve("DUBBING_OPTIMIZE", "translate", ("text",)).model_id, expected)
        self.assertEqual(resolver.resolve("DUBBING_DURATION_REWRITE", "translate", ("text",)).model_id, expected)
if __name__=="__main__": unittest.main()
