const { test, expect } = require('@playwright/test');
const path = require('path');
const fs = require('fs');

const BASE = 'http://127.0.0.1:5070';
const DEFAULT_BASE_URL = process.env.E2E_BASE_URL || process.env.BASE_URL || BASE;
const FIXTURE_EPUB = path.join(__dirname, 'fixture.epub');
const OUTPUT_DIR = path.resolve(__dirname, '..', '..', 'generated_audio');
const DEFAULT_MODEL_ID = 'hexgrad/Kokoro-82M';
const MOCK_JOB_ID = 'mock-job-123';
const DEFAULT_VOICES = ['af_heart', 'af_bella', 'af_nicole'];
const VOX_VOICES = ['vox_alpha', 'vox_beta'];

function jsonResponse(payload, status = 200) {
  return {
    status,
    contentType: 'application/json',
    body: JSON.stringify(payload),
  };
}

function mockModelCatalog() {
  return {
    default_model_id: DEFAULT_MODEL_ID,
    models: [
      {
        id: DEFAULT_MODEL_ID,
        display_name: 'Kokoro 82M (Hugging Face)',
        description: 'Official Kokoro model repository.',
        model_type: 'kokoro',
        model_type_label: 'Kokoro',
        predefined: true,
        download_required: false,
        downloaded: true,
        progress: 100,
        status: 'ready',
        supports_generation: true,
        voices: DEFAULT_VOICES,
      },
    ],
  };
}

function mockVoiceStatus(modelType) {
  const normalizedType = modelType === 'vox' ? 'vox' : modelType === 'other' ? 'other' : 'kokoro';
  const supportsGeneration = normalizedType === 'kokoro';

  return {
    status: {
      id: DEFAULT_MODEL_ID,
      display_name: 'Kokoro 82M (Hugging Face)',
      description: 'Official Kokoro model repository.',
      model_type: normalizedType,
      model_type_label: normalizedType === 'vox' ? 'Vox' : normalizedType === 'other' ? 'Other' : 'Kokoro',
      predefined: true,
      download_required: false,
      downloaded: true,
      progress: 100,
      status: supportsGeneration ? 'ready' : 'downloaded',
      supports_generation: supportsGeneration,
      voices: supportsGeneration ? DEFAULT_VOICES : VOX_VOICES,
    },
  };
}

function mockJobStatus(stage) {
  const stageConfig = {
    queued: {
      status: 'queued',
      progress: 0,
      message: 'Generation queued.',
      active: true,
      can_stop: true,
      started_at: null,
      finished_at: null,
    },
    running: {
      status: 'running',
      progress: 45,
      message: 'Generation in progress.',
      active: true,
      can_stop: true,
      started_at: '2026-04-18T22:18:30Z',
      finished_at: null,
    },
    completed: {
      status: 'completed',
      progress: 100,
      message: 'Generation complete.',
      active: false,
      can_stop: false,
      started_at: '2026-04-18T22:18:30Z',
      finished_at: '2026-04-18T22:19:10Z',
    },
  }[stage];

  return {
    id: MOCK_JOB_ID,
    chapters_count: 1,
    device: 'cpu',
    error: null,
    estimated_seconds: 20,
    hf_model_id: DEFAULT_MODEL_ID,
    run_folder: path.join(OUTPUT_DIR, 'mock-job'),
    stop_requested: false,
    updated_at: '2026-04-18T22:18:30Z',
    can_clear_files: false,
    ...stageConfig,
  };
}

async function mockModelRoutes(page) {
  await page.route(/\/api\/models(?:\?.*)?$/, async (route) => {
    await route.fulfill(jsonResponse(mockModelCatalog()));
  });

  await page.route(/\/api\/models\/voices(?:\?.*)?$/, async (route) => {
    const url = new URL(route.request().url());
    const modelType = url.searchParams.get('model_type') || 'kokoro';
    await route.fulfill(jsonResponse(mockVoiceStatus(modelType)));
  });
}

async function mockUploadRoute(page) {
  await page.route('**/api/upload', async (route) => {
    await route.fulfill(
      jsonResponse({
        job_id: MOCK_JOB_ID,
        chapters_count: 1,
        detected_title: 'Mock Title',
        suggested_name: 'mock_title',
      }),
    );
  });
}

async function mockGenerationRoutes(page) {
  let pollCount = 0;

  await page.route('**/api/generate', async (route) => {
    await route.fulfill(
      jsonResponse({
        ok: true,
        job: {
          id: MOCK_JOB_ID,
          status: 'queued',
        },
      }),
    );
  });

  await page.route(/\/api\/jobs\/[^/]+\/status(?:\?.*)?$/, async (route) => {
    pollCount += 1;
    const stage = pollCount === 1 ? 'queued' : pollCount === 2 ? 'running' : 'completed';
    await route.fulfill(jsonResponse(mockJobStatus(stage)));
  });

  await page.route(/\/api\/jobs\/[^/]+\/files(?:\?.*)?$/, async (route) => {
    await route.fulfill(jsonResponse({ files: [] }));
  });
}

// Helper: ensure fixture epub exists (created by run script) or fail fast
if (!fs.existsSync(FIXTURE_EPUB)) {
  console.error('Missing EPUB fixture:', FIXTURE_EPUB);
}

test.describe('EPUB to Audiobook UI', () => {
  test('single-file happy path', async ({ page, context }) => {
    await page.goto(DEFAULT_BASE_URL);

    // upload EPUB
    const epubInput = page.locator('#epubFile');
    await epubInput.setInputFiles(FIXTURE_EPUB);

    // wait for detected title to update
    await expect(page.locator('#detectedTitle')).not.toHaveText('No file uploaded yet.');

    // set output name and dir
    await page.fill('#outputName', 'e2e_test_output');
    await page.fill('#outputDir', OUTPUT_DIR);

    // start generation
    const generateButton = page.locator('#generateButton');
    await expect(generateButton).toBeEnabled();
    await generateButton.click();

    // wait for completion badge
    await page.waitForSelector('#statusBadge:has-text("completed")', { timeout: 30000 });

    // files list should contain at least one download link
    const downloads = page.locator('#fileList a:has-text("Download")');
    await expect(downloads.first()).toBeVisible();
  });

  test('per-chapter mode generates files', async ({ page }) => {
    await page.goto(DEFAULT_BASE_URL);
    await page.locator('#epubFile').setInputFiles(FIXTURE_EPUB);
    await expect(page.locator('#detectedTitle')).not.toHaveText('No file uploaded yet.');

    // set chapter mode
    await page.locator('input[name="mode"][value="chapter"]').check({ force: true });
    await page.fill('#outputDir', OUTPUT_DIR);
    await page.fill('#outputName', 'e2e_chapters');
    const generateButton = page.locator('#generateButton');
    await expect(generateButton).toBeEnabled();
    await generateButton.click();

    await page.waitForSelector('#statusBadge:has-text("completed")', { timeout: 30000 });

    const items = await page.locator('#fileList li').allTextContents();
    expect(items.length).toBeGreaterThanOrEqual(1);
  });

  test('invalid upload shows error', async ({ page }) => {
    await page.goto(DEFAULT_BASE_URL);
    // create a small text file
    const bad = path.join(__dirname, 'bad.txt');
    fs.writeFileSync(bad, 'not an epub');
    await page.locator('#epubFile').setInputFiles(bad);

    await page.waitForSelector('#statusBadge.failed', { timeout: 5000 });
    await expect(page.locator('#statusMessage')).toContainText('Please provide a .epub file.');
  });

  test('generate button stays disabled during active job', async ({ page }) => {
    await mockModelRoutes(page);
    await mockUploadRoute(page);
    await mockGenerationRoutes(page);

    await page.goto(DEFAULT_BASE_URL);
    await page.locator('#epubFile').setInputFiles(FIXTURE_EPUB);
    await expect(page.locator('#detectedTitle')).toHaveText('Mock Title');

    await page.fill('#outputName', 'mock_output');
    await page.fill('#outputDir', OUTPUT_DIR);

    const generateButton = page.locator('#generateButton');
    await expect(generateButton).toBeEnabled();
    await generateButton.click();

    await expect(generateButton).toBeDisabled();
    await page.waitForSelector('#statusBadge:has-text("queued")', { timeout: 10000 });
    await expect(generateButton).toBeDisabled();
    await expect(page.locator('#generateDisabledReason')).toContainText('Generation queued.');

    await page.waitForSelector('#statusBadge:has-text("running")', { timeout: 10000 });
    await expect(generateButton).toBeDisabled();
    await expect(page.locator('#generateDisabledReason')).toContainText('Generation in progress.');

    await page.waitForSelector('#statusBadge:has-text("completed")', { timeout: 10000 });
    await expect(generateButton).toBeEnabled();
    await expect(page.locator('#generateDisabledReason')).toBeHidden();
  });

  test('generate button blocks unsupported model type', async ({ page }) => {
    await mockModelRoutes(page);
    await mockUploadRoute(page);

    await page.goto(DEFAULT_BASE_URL);
    await page.locator('#epubFile').setInputFiles(FIXTURE_EPUB);
    await expect(page.locator('#detectedTitle')).toHaveText('Mock Title');

    const generateButton = page.locator('#generateButton');
    await expect(generateButton).toBeEnabled();

    await page.selectOption('#modelTypeSelect', 'other');
    await expect(page.locator('#voiceHint')).toContainText('download/select only');
    await expect(generateButton).toBeDisabled();
    await expect(page.locator('#generateDisabledReason')).toContainText('download/select only');
  });
});
