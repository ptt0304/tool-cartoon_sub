"""OpenRouter text-client factory used by production AI features."""

def text_client_factory(provider="openrouter", models=None):
    del provider
    from cartoon_sub.ai.openrouter_client import openrouter_client_factory
    return openrouter_client_factory(models)
