import DocumentIntelligence, {
  getLongRunningPoller,
  isUnexpected,
} from "@azure-rest/ai-document-intelligence";
import { readFile, mkdir, writeFile } from "node:fs/promises";

async function main() {
  const endpoint = process.env.AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT;
  const key = process.env.AZURE_DOCUMENT_INTELLIGENCE_KEY;

  if (!endpoint || !key) {
    throw new Error("Azure endpoint or key is missing from .env.local.");
  }

  // Read the invoice and encode its bytes for the request.
  const base64Source = await readFile("./invoices/IMG_3806.jpeg", {
    encoding: "base64",
  });

  const client = DocumentIntelligence(endpoint, { key });

  console.log("Sending invoice to Azure...");

  const initialResponse = await client
    .path("/documentModels/{modelId}:analyze", "prebuilt-invoice")
    .post({
      contentType: "application/json",
      body: { base64Source },
    });

  if (isUnexpected(initialResponse)) {
    throw new Error(
      initialResponse.body.error?.message ??
        `Azure returned HTTP ${initialResponse.status}.`
    );
  }

  console.log("Waiting for Azure to finish processing...");

  const poller = getLongRunningPoller(client, initialResponse);
  const response = await poller.pollUntilDone();

  if (response.body.status !== "succeeded") {
    throw new Error("Invoice analysis did not succeed.");
  }

  // Save the complete response body without transforming its fields.
  await mkdir("./output", { recursive: true });
  const outputPath = `./output/invoice-${Date.now()}.raw.json`;

  await writeFile(
    outputPath,
    JSON.stringify(response.body, null, 2),
    "utf8"
  );

  console.log(`Raw JSON saved to: ${outputPath}`);
}

main().catch((error) => {
  console.error("Processing failed:", error.message);
  process.exitCode = 1;
});