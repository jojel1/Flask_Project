
"use strict";

// ==================================================
// 1. CONFIGURATION
// ==================================================

const CAMERA_URL = "http://192.168.1.29/stream";

const TELEMETRY_REFRESH_MS = 5000;
const ACTIVITY_REFRESH_MS = 5000;
const HISTORY_REFRESH_MS = 60000;

// API requests use relative URLs.
// They automatically use the same host and port
// as your Flask application (port 5050).

// ==================================================
// 2. HELPER FUNCTIONS
// ==================================================

const $ = (id) => document.getElementById(id);

function csrfToken() {
    const meta = document.querySelector(
        'meta[name="csrf-token"]'
    );

    return meta ? meta.content : "";
}

function formatNumber(value, decimals = 2) {
    if (
        value === null ||
        value === undefined ||
        value === "" ||
        !Number.isFinite(Number(value))
    ) {
        return "--";
    }

    return Number(value).toFixed(decimals);
}

// Keep missing historical values blank.
// Also remove "NOT RECORDED" if it is returned by the API.
function historicalValue(value) {
    if (
        value === null ||
        value === undefined ||
        value === "" ||
        (
            typeof value === "string" &&
            value.trim().toUpperCase() === "NOT RECORDED"
        )
    ) {
        return "";
    }

    return String(value);
}

function formatDate(value) {
    if (!value) return "--";

    const text = String(value);

    // Preserve date-only values without timezone shifting.
    if (/^\d{4}-\d{2}-\d{2}$/.test(text)) {
        return text;
    }

    const date = new Date(value);

    if (Number.isNaN(date.getTime())) {
        return text;
    }

    return date.toLocaleString();
}

function formatFeedingTime(value) {
    if (!value) return "--";

    const text = String(value).trim();

    // Convert 06-00-00 to 06:00:00.
    const normalized = text.replace(/-/g, ":");

    const match = normalized.match(
        /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/
    );

    if (!match) return text;

    const hour = Number(match[1]);
    const minute = match[2];

    if (hour < 0 || hour > 23) return text;

    const suffix = hour >= 12 ? "PM" : "AM";
    const hour12 = hour % 12 || 12;

    return `${hour12}:${minute} ${suffix}`;
}

async function requestJSON(url, options = {}) {
    const response = await fetch(url, {
        credentials: "same-origin",
        cache: "no-store",
        ...options,
        headers: {
            ...(options.headers || {})
        }
    });

    const contentType =
        response.headers.get("content-type") || "";

    if (!contentType.includes("application/json")) {
        throw new Error(
            "The server did not return JSON. " +
            "Check your Flask server, login session, " +
            "and API route."
        );
    }

    const data = await response.json();

    if (!response.ok) {
        throw new Error(
            data.error ||
            data.message ||
            `Request failed (${response.status})`
        );
    }

    return data;
}

function setText(id, value) {
    const element = $(id);

    if (element) {
        element.textContent = value;
    }
}

function showTableMessage(id, message, columns) {
    const tbody = $(id);

    if (!tbody) return;

    const row = document.createElement("tr");
    const cell = document.createElement("td");

    cell.colSpan = columns;
    cell.textContent = message;

    row.appendChild(cell);
    tbody.replaceChildren(row);
}

// ==================================================
// 3. LIVE SENSOR MONITORING
// ==================================================

async function loadTelemetry() {
    const connEl = $("connection-status");

    try {
        const data = await requestJSON(
            "/api/telemetry/latest"
        );

        const telemetry =
            data?.telemetry ??
            data?.record ??
            data;

        if (
            !telemetry ||
            typeof telemetry !== "object" ||
            Array.isArray(telemetry)
        ) {
            setText(
                "connection-status",
                "Waiting for sensor data"
            );

            if (connEl) connEl.style.color = "";

            setText("temperature", "--");
            setText("humidity", "--");
            setText("pig-weight", "--");
            setText("feed-weight", "--");
            setText("target-feed", "Target feed: -- g");
            setText("last-updated", "Last update: --");
            setText("feeder-status", "Waiting for data");

            return;
        }

        const timestamp =
            telemetry.created_at ||
            telemetry.timestamp ||
            telemetry.recorded_at;

        setText(
            "temperature",
            formatNumber(telemetry.temperature_c)
        );

        setText(
            "humidity",
            formatNumber(telemetry.humidity_pct)
        );

        setText(
            "pig-weight",
            formatNumber(telemetry.pig_weight_kg)
        );

        setText(
            "feed-weight",
            formatNumber(telemetry.weigh_bin_weight_g)
        );

        setText(
            "target-feed",
            `Target feed: ${formatNumber(
                telemetry.target_feed_g,
                0
            )} g`
        );

        setText(
            "last-updated",
            `Last update: ${formatDate(timestamp)}`
        );

        // Check whether the latest sensor reading is fresh.
        const ageMs = timestamp
            ? Date.now() - new Date(timestamp).getTime()
            : Infinity;

        const isFresh =
            Number.isFinite(ageMs) &&
            ageMs >= -60000 &&
            ageMs <= 30000;

        if (isFresh) {
            setText("connection-status", "Connected");

            if (connEl) {
                connEl.style.color = "#16a34a";
            }
        } else {
            setText(
                "connection-status",
                "Last reading may be outdated"
            );

            if (connEl) {
                connEl.style.color = "#d97706";
            }
        }

        const status = String(
            telemetry.feeder_status || "unknown"
        ).toLowerCase();

        setText(
            "feeder-status",
            status === "unknown"
                ? "Waiting for device status"
                : status.charAt(0).toUpperCase() +
                  status.slice(1)
        );

    } catch (error) {
        console.error("Telemetry error:", error);

        setText("connection-status", "Unavailable");

        if (connEl) {
            connEl.style.color = "#dc2626";
        }
    }
}

// ==================================================
// 4. RECENT FEEDING ACTIVITY
// ==================================================

async function loadActivity() {
    const tbody = $("activity-list");

    if (!tbody) return;

    try {
        const data = await requestJSON(
            "/api/commands/recent"
        );

        const commands = Array.isArray(data)
            ? data
            : Array.isArray(data?.commands)
                ? data.commands
                : Array.isArray(data?.records)
                    ? data.records
                    : [];

        if (commands.length === 0) {
            showTableMessage(
                "activity-list",
                "No recent manual feeding commands.",
                4
            );

            return;
        }

        tbody.replaceChildren();

        commands.forEach((command) => {
            const row = document.createElement("tr");

            const dateCell = document.createElement("td");
            const amountCell = document.createElement("td");
            const statusCell = document.createElement("td");
            const resultCell = document.createElement("td");

            dateCell.textContent = formatDate(
                command.requested_at ||
                command.created_at
            );

            const grams = Number(command.target_grams);

            amountCell.textContent =
                command.target_grams !== null &&
                command.target_grams !== undefined &&
                command.target_grams !== "" &&
                Number.isFinite(grams)
                    ? `${grams.toLocaleString()} g`
                    : "--";

            const status = String(
                command.status || "unknown"
            ).toLowerCase();

            statusCell.textContent = status;
            statusCell.className =
                `command-status ${status}`;

            const result = command.result;

            if (
                typeof result === "string" &&
                result.trim()
            ) {
                resultCell.textContent = result;

            } else if (
                result &&
                typeof result === "object"
            ) {
                resultCell.textContent =
                    result.message ||
                    result.result ||
                    JSON.stringify(result);

            } else {
                resultCell.textContent =
                    status === "pending"
                        ? "Waiting for ESP32"
                        : status === "running"
                            ? "Feeding in progress"
                            : status === "completed"
                                ? "Completed"
                                : status === "failed"
                                    ? "Failed"
                                    : "--";
            }

            row.append(
                dateCell,
                amountCell,
                statusCell,
                resultCell
            );

            tbody.appendChild(row);
        });

    } catch (error) {
        console.error("Activity error:", error);

        showTableMessage(
            "activity-list",
            "Unable to load recent feeding activity.",
            4
        );
    }
}

// ==================================================
// 5. HISTORICAL FEEDING RECORDS
// ==================================================

async function loadRecoveredFeedingHistory() {
    const tbody = $("historicalRecordsBody");

    if (!tbody) {
        console.error(
            'Historical table body "historicalRecordsBody" ' +
            "was not found in dashboard.html."
        );

        return;
    }

    try {
        const data = await requestJSON(
            "/api/feeding/recovered"
        );

        if (data?.success !== true) {
            throw new Error(
                data?.message ||
                data?.error ||
                "Historical records could not be loaded."
            );
        }

        const records = Array.isArray(data.records)
            ? data.records
            : [];

        if (records.length === 0) {
            showTableMessage(
                "historicalRecordsBody",
                "No historical feeding records found.",
                6
            );

            return;
        }

        tbody.replaceChildren();

        records.forEach((record) => {
            const row = document.createElement("tr");

            // ------------------------------------------
            // 1. DATE
            // ------------------------------------------

            const dateValue = historicalValue(
                record.feeding_date
            );

            // ------------------------------------------
            // 2. FEEDING TIME
            // ------------------------------------------

            const timeValue = historicalValue(
                record.feeding_time
            );

            // ------------------------------------------
            // 3. PIG WEIGHT
            // ------------------------------------------

            const weightValue =
                record.pig_weight_kg === null ||
                record.pig_weight_kg === undefined ||
                record.pig_weight_kg === ""
                    ? ""
                    : formatNumber(
                        record.pig_weight_kg,
                        2
                    );

            // ------------------------------------------
            // 4. WEIGHT RANGE CATEGORY
            // ------------------------------------------

            const categoryValue = historicalValue(
                record.weight_range_category
            );

            // ------------------------------------------
            // 5. FEED AMOUNT
            // ------------------------------------------

            const feedValue =
                record.feed_amount_kg === null ||
                record.feed_amount_kg === undefined ||
                record.feed_amount_kg === ""
                    ? ""
                    : `${formatNumber(
                        record.feed_amount_kg,
                        3
                    )} kg`;

            // ------------------------------------------
            // 6. RESULT / RECORD SOURCE
            // ------------------------------------------

            // Blank if result is missing or says NOT RECORDED.
            const finalResult = historicalValue(
                record.result ?? record.record_source
            );

            // ------------------------------------------
            // CREATE TABLE CELLS
            // ------------------------------------------

            const values = [
                dateValue,
                timeValue,
                weightValue,
                categoryValue,
                feedValue,
                finalResult
            ];

            values.forEach((value) => {
                const cell = document.createElement("td");

                cell.textContent = String(value);

                row.appendChild(cell);
            });

            tbody.appendChild(row);
        });

        console.info(
            `Historical feeding records loaded: ${records.length}`
        );

    } catch (error) {
        console.error(
            "Historical records error:",
            error
        );

        showTableMessage(
            "historicalRecordsBody",
            `Unable to load historical records: ${error.message}`,
            6
        );
    }
}

// ==================================================
// 6. MANUAL FEEDING
// ==================================================

let feedRequestInProgress = false;

async function submitFeedRequest(event) {
    event.preventDefault();

    if (feedRequestInProgress) return;

    const input = $("feed-amount");
    const button = $("feed-button");

    const grams = Number(input?.value);

    if (
        !Number.isInteger(grams) ||
        grams < 1 ||
        grams > 5000
    ) {
        setText(
            "feed-message",
            "Enter a whole number between 1 and 5000 grams."
        );

        return;
    }

    feedRequestInProgress = true;

    if (button) {
        button.disabled = true;
        button.textContent = "Sending...";
    }

    setText(
        "feed-message",
        "Sending feeding command..."
    );

    try {
        const data = await requestJSON(
            "/api/commands",
            {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-CSRFToken": csrfToken()
                },
                body: JSON.stringify({
                    command: "dispense",
                    target_grams: grams
                })
            }
        );

        setText(
            "feed-message",
            data.message ||
            `Feeding command queued for ${grams} g.`
        );

        await loadActivity();

        setTimeout(loadActivity, 1000);
        setTimeout(loadActivity, 3000);

    } catch (error) {
        console.error(
            "Feeding request error:",
            error
        );

        setText(
            "feed-message",
            `Unable to submit feeding command: ${error.message}`
        );

    } finally {
        feedRequestInProgress = false;

        if (button) {
            button.disabled = false;
            button.textContent = "Feed Now";
        }
    }
}

// ==================================================
// 7. ESP32-CAM
// ==================================================

function initializeCamera() {
    const camera = $("esp32-camera");
    const placeholder = $("camera-placeholder");

    if (!camera) return;

    function showCameraError() {
        camera.style.display = "none";

        if (placeholder) {
            placeholder.style.display = "flex";
        }

        setText(
            "camera-message",
            "Unable to load camera stream. " +
            "Check the camera IP and network."
        );

        setText(
            "camera-status",
            "Camera unavailable"
        );
    }

    camera.addEventListener("load", () => {
        if (placeholder) {
            placeholder.style.display = "none";
        }

        camera.style.display = "block";

        setText(
            "camera-status",
            "Camera stream connected"
        );
    });

    camera.addEventListener("error", showCameraError);

    camera.src = CAMERA_URL;
}

// ==================================================
// 8. INITIALIZE DASHBOARD
// ==================================================

document.addEventListener("DOMContentLoaded", () => {
    const feedForm = $("feed-form");

    if (feedForm) {
        feedForm.addEventListener(
            "submit",
            submitFeedRequest
        );
    }

    initializeCamera();

    loadTelemetry();
    loadActivity();
    loadRecoveredFeedingHistory();

    setInterval(
        loadTelemetry,
        TELEMETRY_REFRESH_MS
    );

    setInterval(
        loadActivity,
        ACTIVITY_REFRESH_MS
    );

    setInterval(
        loadRecoveredFeedingHistory,
        HISTORY_REFRESH_MS
    );
});
