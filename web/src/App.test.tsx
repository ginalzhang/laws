import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { App } from './App';

function renderApp() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

describe('Review queue app', () => {
  beforeEach(() => {
    window.localStorage.clear();
    vi.restoreAllMocks();
  });

  afterEach(() => {
    cleanup();
  });

  test('shows a session warning without the legacy token', () => {
    renderApp();
    expect(screen.getByText('No active session token found.')).toBeInTheDocument();
  });

  test('renders packet detail and review rows from the API', async () => {
    window.localStorage.setItem('pv_token', 'test-token');
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url === '/review/packets') {
        return Response.json([
          {
            id: 1,
            original_name: 'packet.jpg',
            uploaded_at: '2026-05-14T12:00:00',
            status: 'done',
            total_lines: 1,
            new_sigs: 1,
            already_counted: 0,
            needs_review: 0,
            worker_id: 7,
          },
        ]);
      }
      if (url === '/review/packets/1') {
        return Response.json({
          id: 1,
          original_name: 'packet.jpg',
          uploaded_at: '2026-05-14T12:00:00',
          status: 'done',
          total_lines: 1,
          new_sigs: 1,
          already_counted: 0,
          needs_review: 0,
          worker_id: 7,
          has_cleaned: false,
          summary: {},
          voter_roll_text: '',
          county: '',
          lines: [
            {
              id: 11,
              line_no: 1,
              row_status: 'new_signature',
              raw_name: 'Jane Smith',
              norm_name: 'Jane Smith',
              raw_address: '123 Main St',
              norm_address: '123 Main St',
              raw_city: 'Pasadena',
              raw_zip: '91101',
              valid_zip: true,
              raw_date: '05/14/2026',
              has_signature: true,
              ai_verdict: 'likely_valid',
              flags: [],
              voter_status: 'valid',
              voter_confidence: 98,
              voter_reason: '',
              fraud_flags: [],
              fraud_score: 0,
              review_decision: null,
              action: null,
              reviewed_at: null,
            },
          ],
        });
      }
      if (url === '/review/counties') {
        return Response.json(['Los Angeles']);
      }
      if (url === '/review/packets/1/image?type=raw') {
        return new Response(new Blob(['image']));
      }
      return new Response('{}', { status: 404 });
    });

    renderApp();

    await waitFor(() => expect(screen.getByText('Jane Smith')).toBeInTheDocument());
    expect(screen.getAllByText('packet.jpg').length).toBeGreaterThan(0);
    expect(screen.getByText('valid (98%)')).toBeInTheDocument();
  });
});
