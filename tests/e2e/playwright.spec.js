const { test, expect } = require('@playwright/test');
const path = require('path');
const fs = require('fs');

const BASE = 'http://127.0.0.1:5070';
const FIXTURE_EPUB = path.join(__dirname, 'fixture.epub');
const OUTPUT_DIR = path.resolve(__dirname, '..', '..', 'generated_audio');

// Helper: ensure fixture epub exists (created by run script) or fail fast
if (!fs.existsSync(FIXTURE_EPUB)) {
  console.error('Missing EPUB fixture:', FIXTURE_EPUB);
}

test.describe('EPUB to Audiobook UI', () => {
  test('single-file happy path', async ({ page, context }) => {
    await page.goto(BASE);

    // upload EPUB
    const epubInput = page.locator('#epubFile');
    await epubInput.setInputFiles(FIXTURE_EPUB);

    // wait for detected title to update
    await expect(page.locator('#detectedTitle')).not.toHaveText('No file uploaded yet.');

    // set output name and dir
    await page.fill('#outputName', 'e2e_test_output');
    await page.fill('#outputDir', OUTPUT_DIR);

    // start generation
    await page.click('#generateButton');

    // wait for completion badge
    await page.waitForSelector('#statusBadge:has-text("completed")', { timeout: 30000 });

    // files list should contain at least one download link
    const downloads = page.locator('#fileList a:has-text("Download")');
    await expect(downloads.first()).toBeVisible();
  });

  test('per-chapter mode generates files', async ({ page }) => {
    await page.goto(BASE);
    await page.locator('#epubFile').setInputFiles(FIXTURE_EPUB);
    await expect(page.locator('#detectedTitle')).not.toHaveText('No file uploaded yet.');

    // set chapter mode
    await page.check('input[name="mode"][value="chapter"]');
    await page.fill('#outputDir', OUTPUT_DIR);
    await page.fill('#outputName', 'e2e_chapters');
    await page.click('#generateButton');

    await page.waitForSelector('#statusBadge:has-text("completed")', { timeout: 30000 });

    const items = await page.locator('#fileList li').allTextContents();
    expect(items.length).toBeGreaterThanOrEqual(1);
  });

  test('invalid upload shows error', async ({ page }) => {
    await page.goto(BASE);
    // create a small text file
    const bad = path.join(__dirname, 'bad.txt');
    fs.writeFileSync(bad, 'not an epub');
    await page.locator('#epubFile').setInputFiles(bad);

    await page.waitForSelector('#statusBadge.failed', { timeout: 5000 });
    await expect(page.locator('#statusMessage')).toContainText('Please provide a .epub file.');
  });
});
