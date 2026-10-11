def production_models(openrouter_models, *, openrouter_usable=True, capability="text"):
    rows = []
    if openrouter_usable:
        for item in openrouter_models or []:
            architecture = item.get("architecture", {})
            inputs = {str(v).casefold() for v in architecture.get("input_modalities", [])}
            outputs = {str(v).casefold() for v in architecture.get("output_modalities", [])}
            if capability == "text" and not ("text" in inputs and ("text" in outputs or not outputs)): continue
            if capability == "transcription" and "transcription" not in outputs: continue
            if capability == "vision_frames" and not ({"image", "video"} & inputs): continue
            rows.append({**item, "provider": "openrouter", "name": item.get("name") or item.get("id")})
    return rows
