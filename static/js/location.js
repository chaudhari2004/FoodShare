
document.addEventListener("DOMContentLoaded", function () {
    const mapElement = document.getElementById("map");
    const gpsButton = document.getElementById("getLocationBtn");
    const locationMessage = document.getElementById("locationMessage");

    const latitudeInput = document.getElementById("latitude");
    const longitudeInput = document.getElementById("longitude");

    const latitudeDisplay = document.getElementById("latitudeDisplay");
    const longitudeDisplay = document.getElementById("longitudeDisplay");

    const pickupAddress = document.getElementById("pickup_location");

    if (!mapElement || !gpsButton || !locationMessage) return;

    if (typeof L === "undefined") {
        locationMessage.textContent =
            "Map library could not load. Enter your address manually.";
        gpsButton.disabled = true;
        return;
    }

    // Default view only; this is not the user's current location.
    const map = L.map("map").setView([20.5937, 78.9629], 5);

    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution:
            '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    let marker = null;
    let lookupNumber = 0;

    // Convert coordinates to a readable address.
    async function getAddressFromCoordinates(latitude, longitude) {
        const currentLookup = ++lookupNumber;

        locationMessage.textContent = "Finding address...";

        try {
            const url = new URL(
                "https://nominatim.openstreetmap.org/reverse"
            );

            url.search = new URLSearchParams({
                format: "jsonv2",
                lat: latitude,
                lon: longitude,
                zoom: 18,
                addressdetails: 1
            });

            const response = await fetch(url);

            if (!response.ok) {
                throw new Error("Address lookup failed");
            }

            const data = await response.json();

            // Ignore an older response if the marker has moved again.
            if (currentLookup !== lookupNumber) return;

            if (data.display_name) {
                // Fill the field automatically.
                // The donor can edit this value afterwards.
                pickupAddress.value = data.display_name;

                locationMessage.textContent =
                    "Address filled automatically. You can edit it manually.";
            } else {
                locationMessage.textContent =
                    "Address not found. Please enter it manually.";
            }
        } catch (error) {
            if (currentLookup !== lookupNumber) return;

            locationMessage.textContent =
                "Could not fetch the address. Please enter it manually.";
        }
    }

    // Select map coordinates and update the marker.
    function selectLocation(latitude, longitude) {
        latitudeInput.value = latitude.toFixed(7);
        longitudeInput.value = longitude.toFixed(7);

        latitudeDisplay.textContent = latitude.toFixed(7);
        longitudeDisplay.textContent = longitude.toFixed(7);

        const position = [latitude, longitude];

        if (marker) {
            marker.setLatLng(position);
        } else {
            marker = L.marker(position, {
                draggable: true
            }).addTo(map);

            marker.bindPopup("Food pickup location");

            marker.on("dragend", function (event) {
                const point = event.target.getLatLng();

                selectLocation(point.lat, point.lng);
            });
        }

        getAddressFromCoordinates(latitude, longitude);
    }

    // Select a location by clicking on the map.
    map.on("click", function (event) {
        selectLocation(event.latlng.lat, event.latlng.lng);
    });

    // Get current GPS location.
    gpsButton.addEventListener("click", function () {
        if (!navigator.geolocation) {
            locationMessage.textContent =
                "GPS is not supported. Select a map point or enter the address manually.";
            return;
        }

        gpsButton.disabled = true;
        gpsButton.textContent = "Getting location...";

        locationMessage.textContent =
            "Please allow location access in your browser.";

        navigator.geolocation.getCurrentPosition(
            function (position) {
                const latitude = position.coords.latitude;
                const longitude = position.coords.longitude;

                map.setView([latitude, longitude], 16);
                selectLocation(latitude, longitude);

                if (marker) marker.openPopup();

                gpsButton.disabled = false;
                gpsButton.textContent = "📍 Use My Current Location";
            },

            function (error) {
                if (error.code === error.PERMISSION_DENIED) {
                    locationMessage.textContent =
                        "Location permission denied. Allow browser location access or select a map point.";
                } else if (error.code === error.TIMEOUT) {
                    locationMessage.textContent =
                        "GPS timed out. Please try again or select a map point.";
                } else {
                    locationMessage.textContent =
                        "Could not get your location. Select a map point or enter the address manually.";
                }

                gpsButton.disabled = false;
                gpsButton.textContent = "📍 Use My Current Location";
            },

            {
                enableHighAccuracy: true,
                timeout: 15000,
                maximumAge: 0
            }
        );
    });

    // Ensure the map renders at the correct size.
    setTimeout(function () {
        map.invalidateSize();
    }, 200);
});
