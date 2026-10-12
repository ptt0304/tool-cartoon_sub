# Google Gemini Official — Settings Phase 1

**Settings → AI → Google Gemini Official** only manages Gemini Developer API keys and discovers the native Google model catalog. It does not yet select Gemini Native for transcription, translation, review, visual analysis, subtitle segmentation or any production job.

Store one key per `gemini_key:` line in the local ignored `api_key.txt`. Resolution order is: explicit `gemini_key` entries in that file, OS keyring, then `GEMINI_API_KEY` or `GOOGLE_API_KEY`. Keys are masked in the UI.

**Test All Keys** calls only Google's `models.list` endpoint. **Refresh Free Models** retrieves the paginated model catalog and caches it for six hours. The catalog keeps the last successful cache if refresh fails. Google does not publish pricing in every Models API response; only models matched by the versioned Google AI Studio free-tier rules are shown as verified free. All others stay **Unknown**, never silently free. Model availability and free-tier eligibility can change.
