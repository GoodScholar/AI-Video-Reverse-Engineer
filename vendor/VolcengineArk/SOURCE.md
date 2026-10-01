# Volcengine Ark image protocol source

- Repository: https://github.com/volcengine/volcengine-python-sdk
- Fixed commit: `629d09880eff23a283a2af1dd98b65294776e614`
- License: Apache-2.0; see LICENSE.txt.
- Source read: `volcenginesdkarkruntime/resources/images/images.py`, `volcenginesdkarkruntime/types/images/images.py`.
- Adaptation: `backend/app/image_generation.py` sends the SDK-defined `/images/generations` JSON request with model, prompt, pixel size, `response_format=b64_json`, and one or multiple image data URLs.
- The SDK itself is not vendored or imported. The project uses Python 3.9 and its existing synchronous HTTPX/configuration/asset-validation boundaries; modern SDK types and transport are not copied. This is protocol adaptation, not a claim that the entire SDK was reused.
- Actual account/model availability, model-specific size limits, and paid generation remain unverified until a dedicated image API is configured. Chat model credentials are not reused implicitly.
