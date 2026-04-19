# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: playwright.spec.js >> EPUB to Audiobook UI >> generate button blocks unsupported model type
- Location: playwright.spec.js:256:3

# Error details

```
Error: expect(locator).toContainText(expected) failed

Locator: locator('#generateDisabledReason')
Expected substring: "download/select only"
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toContainText" with timeout 5000ms
  - waiting for locator('#generateDisabledReason')

```

# Page snapshot

```yaml
- generic [active] [ref=e1]:
  - banner [ref=e2]:
    - generic [ref=e3]:
      - heading "EPUB to Audiobook" [level=1] [ref=e4]
      - generic [ref=e5]:
        - generic [ref=e6]: "Device:"
        - generic [ref=e7]: cuda
  - main [ref=e8]:
    - generic [ref=e9]:
      - generic [ref=e10]:
        - heading "1. Choose Your EPUB" [level=2] [ref=e11]
        - button "Click or drag an EPUB file here" [ref=e12] [cursor=pointer]:
          - img [ref=e13]
          - paragraph [ref=e15]: Click to upload or drag an EPUB here
          - paragraph [ref=e16]: Only .epub files are accepted
          - button "Click or drag an EPUB file here"
        - generic [ref=e17]:
          - generic [ref=e18]:
            - text: "Title:"
            - strong [ref=e19]: Mock Title
          - generic [ref=e20]: "Estimated output files: 1 file"
      - generic [ref=e21]:
        - heading "2. Settings" [level=2] [ref=e22]
        - generic [ref=e23]:
          - generic [ref=e24]:
            - generic [ref=e25]: Output Name
            - textbox "Output Name" [ref=e26]:
              - /placeholder: audiobook
              - text: mock_title
          - generic [ref=e27]:
            - generic [ref=e28]: Model
            - combobox "Model" [ref=e29]:
              - option "Kokoro 82M (Hugging Face) (Kokoro) - ready" [selected]
            - generic [ref=e30]: Kokoro is the default. Download your selected model before generation.
          - generic [ref=e31]:
            - generic [ref=e32]: Model Type
            - combobox "Model Type" [ref=e33]:
              - option "Kokoro"
              - option "Vox" [selected]
              - option "Other"
            - generic [ref=e34]: Choose a type first, then pick a voice from that model.
          - generic [ref=e35]:
            - generic [ref=e36]: Manual Hugging Face Model ID (optional)
            - textbox "Manual Hugging Face Model ID (optional)" [ref=e37]:
              - /placeholder: organization/model
              - text: hexgrad/Kokoro-82M
            - generic [ref=e38]:
              - text: Leave blank to download the model selected above. Manual entries are cached in
              - code [ref=e39]: .app_data/hf_models
              - text: .
          - generic [ref=e40]:
            - button "Download Model" [ref=e41] [cursor=pointer]
            - generic [ref=e42]: This model type is download/select only right now. Generation currently supports Kokoro models.
          - generic [ref=e45]:
            - generic [ref=e46]: Voice
            - combobox "Voice" [ref=e47]:
              - option "vox_alpha" [selected]
              - option "vox_beta"
            - generic [ref=e48]: Selected model type is download/select only right now. Generation currently supports Kokoro models.
          - generic [ref=e49]:
            - generic [ref=e50]: Output Directory
            - textbox "Output Directory" [ref=e51]: /home/lance/Documents/Code/AudiobookFromEpub/generated_audio
          - generic [ref=e52]:
            - generic [ref=e53]: Front Matter Filter
            - combobox "Front Matter Filter" [ref=e54]:
              - option "Default — Skip common front/back matter (recommended)" [selected]
              - option "Conservative — Only remove obvious TOC pages"
              - option "Aggressive — Also skip introductions and notes"
              - option "Off — Include everything as-is"
          - generic [ref=e55]:
            - generic [ref=e56]: Generation Mode
            - generic [ref=e57]:
              - generic [ref=e58] [cursor=pointer]:
                - radio "Single File — one continuous audiobook" [checked] [ref=e59]
                - generic [ref=e60]: Single File — one continuous audiobook
              - generic [ref=e61] [cursor=pointer]:
                - radio "By Chapter — separate file per chapter" [ref=e62]
                - generic [ref=e63]: By Chapter — separate file per chapter
      - button "Generate Audiobook" [disabled] [ref=e64]
    - generic [ref=e65]:
      - generic [ref=e66]:
        - generic [ref=e67]:
          - heading "Status" [level=2] [ref=e68]
          - generic [ref=e69]: Uploaded
        - paragraph [ref=e70]: Upload complete. 1 chapters detected.
      - generic [ref=e73]:
        - heading "Generated Files" [level=2] [ref=e75]
        - list [ref=e77]:
          - listitem [ref=e78]: No generated files yet.
```

# Test source

```ts
  170 |     await page.goto(DEFAULT_BASE_URL);
  171 | 
  172 |     // upload EPUB
  173 |     const epubInput = page.locator('#epubFile');
  174 |     await epubInput.setInputFiles(FIXTURE_EPUB);
  175 | 
  176 |     // wait for detected title to update
  177 |     await expect(page.locator('#detectedTitle')).not.toHaveText('No file uploaded yet.');
  178 | 
  179 |     // set output name and dir
  180 |     await page.fill('#outputName', 'e2e_test_output');
  181 |     await page.fill('#outputDir', OUTPUT_DIR);
  182 | 
  183 |     // start generation
  184 |     const generateButton = page.locator('#generateButton');
  185 |     await expect(generateButton).toBeEnabled();
  186 |     await generateButton.click();
  187 | 
  188 |     // wait for completion badge
  189 |     await page.waitForSelector('#statusBadge:has-text("completed")', { timeout: 30000 });
  190 | 
  191 |     // files list should contain at least one download link
  192 |     const downloads = page.locator('#fileList a:has-text("Download")');
  193 |     await expect(downloads.first()).toBeVisible();
  194 |   });
  195 | 
  196 |   test('per-chapter mode generates files', async ({ page }) => {
  197 |     await page.goto(DEFAULT_BASE_URL);
  198 |     await page.locator('#epubFile').setInputFiles(FIXTURE_EPUB);
  199 |     await expect(page.locator('#detectedTitle')).not.toHaveText('No file uploaded yet.');
  200 | 
  201 |     // set chapter mode
  202 |     await page.locator('input[name="mode"][value="chapter"]').check({ force: true });
  203 |     await page.fill('#outputDir', OUTPUT_DIR);
  204 |     await page.fill('#outputName', 'e2e_chapters');
  205 |     const generateButton = page.locator('#generateButton');
  206 |     await expect(generateButton).toBeEnabled();
  207 |     await generateButton.click();
  208 | 
  209 |     await page.waitForSelector('#statusBadge:has-text("completed")', { timeout: 30000 });
  210 | 
  211 |     const items = await page.locator('#fileList li').allTextContents();
  212 |     expect(items.length).toBeGreaterThanOrEqual(1);
  213 |   });
  214 | 
  215 |   test('invalid upload shows error', async ({ page }) => {
  216 |     await page.goto(DEFAULT_BASE_URL);
  217 |     // create a small text file
  218 |     const bad = path.join(__dirname, 'bad.txt');
  219 |     fs.writeFileSync(bad, 'not an epub');
  220 |     await page.locator('#epubFile').setInputFiles(bad);
  221 | 
  222 |     await page.waitForSelector('#statusBadge.failed', { timeout: 5000 });
  223 |     await expect(page.locator('#statusMessage')).toContainText('Please provide a .epub file.');
  224 |   });
  225 | 
  226 |   test('generate button stays disabled during active job', async ({ page }) => {
  227 |     await mockModelRoutes(page);
  228 |     await mockUploadRoute(page);
  229 |     await mockGenerationRoutes(page);
  230 | 
  231 |     await page.goto(DEFAULT_BASE_URL);
  232 |     await page.locator('#epubFile').setInputFiles(FIXTURE_EPUB);
  233 |     await expect(page.locator('#detectedTitle')).toHaveText('Mock Title');
  234 | 
  235 |     await page.fill('#outputName', 'mock_output');
  236 |     await page.fill('#outputDir', OUTPUT_DIR);
  237 | 
  238 |     const generateButton = page.locator('#generateButton');
  239 |     await expect(generateButton).toBeEnabled();
  240 |     await generateButton.click();
  241 | 
  242 |     await expect(generateButton).toBeDisabled();
  243 |     await page.waitForSelector('#statusBadge:has-text("queued")', { timeout: 10000 });
  244 |     await expect(generateButton).toBeDisabled();
  245 |     await expect(page.locator('#generateDisabledReason')).toContainText('Generation queued.');
  246 | 
  247 |     await page.waitForSelector('#statusBadge:has-text("running")', { timeout: 10000 });
  248 |     await expect(generateButton).toBeDisabled();
  249 |     await expect(page.locator('#generateDisabledReason')).toContainText('Generation in progress.');
  250 | 
  251 |     await page.waitForSelector('#statusBadge:has-text("completed")', { timeout: 10000 });
  252 |     await expect(generateButton).toBeEnabled();
  253 |     await expect(page.locator('#generateDisabledReason')).toBeHidden();
  254 |   });
  255 | 
  256 |   test('generate button blocks unsupported model type', async ({ page }) => {
  257 |     await mockModelRoutes(page);
  258 |     await mockUploadRoute(page);
  259 | 
  260 |     await page.goto(DEFAULT_BASE_URL);
  261 |     await page.locator('#epubFile').setInputFiles(FIXTURE_EPUB);
  262 |     await expect(page.locator('#detectedTitle')).toHaveText('Mock Title');
  263 | 
  264 |     const generateButton = page.locator('#generateButton');
  265 |     await expect(generateButton).toBeEnabled();
  266 | 
  267 |     await page.selectOption('#modelTypeSelect', 'vox');
  268 |     await expect(page.locator('#voiceHint')).toContainText('download/select only');
  269 |     await expect(generateButton).toBeDisabled();
> 270 |     await expect(page.locator('#generateDisabledReason')).toContainText('download/select only');
      |                                                           ^ Error: expect(locator).toContainText(expected) failed
  271 |   });
  272 | });
  273 | 
```