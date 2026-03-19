import os
import cloudinary.uploader
from django.conf import settings
from django.core.wsgi import get_wsgi_application

# Setup Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "churchforce.settings")
application = get_wsgi_application()

# Set your local media directory
MEDIA_ROOT = os.path.join(settings.BASE_DIR, 'media')

# Walk through files
for root, dirs, files in os.walk(MEDIA_ROOT):
    for file in files:
        file_path = os.path.join(root, file)

        relative_path = os.path.relpath(file_path, MEDIA_ROOT)
        public_id = os.path.splitext(relative_path)[0].replace("\\", "/")  # remove extension + normalize slashes

        try:
            result = cloudinary.uploader.upload(file_path, public_id=public_id, overwrite=False)
            print(f"✅ Uploaded: {file_path} → {result['secure_url']}")
        except Exception as e:
            print(f"❌ Failed to upload {file_path}: {e}")
