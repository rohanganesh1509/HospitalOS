const bankerButton = document.getElementById("runBanker");
bankerButton?.addEventListener("click", async () => {
  const output = document.getElementById("bankerResult");
  bankerButton.disabled = true;
  bankerButton.textContent = "Checking...";
  try {
    const parse = id => JSON.parse(document.getElementById(id).value);
    const response = await fetch("/api/bankers", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({allocation: parse("allocation"), maximum: parse("maximum"), available: parse("available")})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Invalid input.");
    output.className = "result-message " + (data.safe ? "success" : "error");
    output.textContent = data.safe
      ? `${data.message} Safe sequence: ${data.safe_sequence.join(" → ")}`
      : data.message;
    document.getElementById("needMatrix").textContent = JSON.stringify(data.need, null, 2);
    document.getElementById("needArea").classList.remove("hidden");
  } catch (error) {
    output.className = "result-message error";
    output.textContent = "Check your JSON format and matrix dimensions. " + error.message;
  } finally {
    bankerButton.disabled = false;
    bankerButton.textContent = "Run safety check";
  }
});
