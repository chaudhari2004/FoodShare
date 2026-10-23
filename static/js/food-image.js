
document.addEventListener("DOMContentLoaded", () => {
    const galleryInput = document.getElementById("foodImageInput");
    const cameraInput = document.getElementById("foodCameraInput");
    const chooseButton = document.getElementById("chooseFoodImage");
    const cameraButton = document.getElementById("takeFoodPhoto");
    const preview = document.getElementById("foodImagePreview");
    const placeholder = document.getElementById("foodImagePlaceholder");
    const removeButton = document.getElementById("removeFoodImage");
    const message = document.getElementById("foodImageMessage");

    if (
        !galleryInput || !cameraInput || !chooseButton ||
        !cameraButton || !preview || !placeholder ||
        !removeButton || !message
    ) {
        console.error("Food image upload elements are missing.");
        return;
    }

    const MAX_SIZE = 5 * 1024 * 1024;
    const allowedTypes = ["image/jpeg", "image/png", "image/webp"];
    let previewUrl = null;

    function showMessage(text, isError = false) {
        message.textContent = text;
        message.classList.toggle("is-error", isError);
    }

    function clearPreview() {
        if (previewUrl) {
            URL.revokeObjectURL(previewUrl);
            previewUrl = null;
        }

        preview.removeAttribute("src");
        preview.hidden = true;
        placeholder.hidden = false;
        removeButton.hidden = true;
    }

    function handleFile(file, sourceInput) {
        if (!file) return;

        if (!allowedTypes.includes(file.type)) {
            sourceInput.value = "";
            showMessage("Choose a JPG, PNG or WEBP image.", true);
            return;
        }

        if (file.size === 0) {
            sourceInput.value = "";
            showMessage("The selected image is empty.", true);
            return;
        }

        if (file.size > MAX_SIZE) {
            sourceInput.value = "";
            showMessage("Image must be 5 MB or smaller.", true);
            return;
        }

        // Keep only the selected image.
        const otherInput =
            sourceInput === galleryInput ? cameraInput : galleryInput;

        otherInput.value = "";

        clearPreview();

        previewUrl = URL.createObjectURL(file);
        preview.src = previewUrl;
        preview.hidden = false;
        placeholder.hidden = true;
        removeButton.hidden = false;

        preview.onerror = () => {
            clearPreview();
            sourceInput.value = "";
            showMessage("Could not preview this image. Please choose another.", true);
        };

        showMessage(`Selected: ${file.name}`);
    }

    chooseButton.addEventListener("click", () => galleryInput.click());
    cameraButton.addEventListener("click", () => cameraInput.click());

    galleryInput.addEventListener("change", () => {
        handleFile(galleryInput.files[0], galleryInput);
    });

    cameraInput.addEventListener("change", () => {
        handleFile(cameraInput.files[0], cameraInput);
    });

    removeButton.addEventListener("click", () => {
        galleryInput.value = "";
        cameraInput.value = "";
        clearPreview();
        showMessage("Photo removed. You can choose another photo.");
    });

    window.addEventListener("beforeunload", () => {
        if (previewUrl) {
            URL.revokeObjectURL(previewUrl);
        }
    });
});
