import { expect, test } from '@playwright/test';

test('review queue shell renders', async ({ page }) => {
  await page.goto('/app/review/');
  await expect(page.getByRole('heading', { name: 'Review Queue' })).toBeVisible();
  await expect(page.getByText('No active session found.')).toBeVisible();
});
