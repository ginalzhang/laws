import { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Check,
  Download,
  FileText,
  Loader2,
  RefreshCcw,
  Save,
  Search,
  ShieldAlert,
  X,
} from 'lucide-react';
import { api, type PacketDetail, type PacketLine, type PacketListItem } from './api/client';

type Action = 'approved' | 'rejected' | 'escalated';
type Decision = 'confirmed_fraud' | 'cleared';

const statusMeta: Record<string, { label: string; className: string }> = {
  new_signature: { label: 'New', className: 'bg-emerald-100 text-emerald-700' },
  already_counted: { label: 'Already counted', className: 'bg-zinc-100 text-zinc-500' },
  changed_needs_review: { label: 'Needs review', className: 'bg-amber-100 text-amber-800' },
  blank: { label: 'Blank', className: 'bg-zinc-100 text-zinc-400' },
};

function formatDate(value?: string | null): string {
  if (!value) return '-';
  return new Date(`${value}Z`).toLocaleDateString([], { month: 'short', day: 'numeric' });
}

function centsLabel(value?: number | null): string {
  return value == null ? '-' : String(value);
}

function StatusBadge({ status }: { status?: string | null }) {
  const meta = statusMeta[status ?? 'blank'] ?? statusMeta.blank;
  return (
    <span className={`inline-flex h-6 items-center rounded-md px-2 text-xs font-semibold ${meta.className}`}>
      {meta.label}
    </span>
  );
}

function PacketCard({
  packet,
  active,
  onSelect,
}: {
  packet: PacketListItem;
  active: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={`w-full rounded-lg border bg-white p-3 text-left transition hover:border-zinc-400 ${
        active ? 'border-blue-500 bg-blue-50' : 'border-line'
      }`}
    >
      <div className="break-all text-sm font-semibold text-ink">{packet.original_name || `Packet #${packet.id}`}</div>
      <div className="mt-1 text-xs text-muted">
        {formatDate(packet.uploaded_at)} · {packet.total_lines ?? '?'} lines
      </div>
      <span className={`mt-2 inline-flex rounded-md px-2 py-1 text-xs font-semibold status-${packet.status}`}>
        {packet.status}
      </span>
    </button>
  );
}

function ImagePreview({ packet }: { packet: PacketDetail }) {
  const [src, setSrc] = useState<string>('');
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let active = true;
    let objectUrl = '';
    setFailed(false);
    setSrc('');
    api
      .imageBlob(packet.id, packet.has_cleaned ? 'cleaned' : 'raw')
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch(() => {
        if (active) setFailed(true);
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [packet.id, packet.has_cleaned]);

  return (
    <div className="flex min-h-56 items-center justify-center overflow-hidden rounded-lg bg-stone-100">
      {src ? <img src={src} alt="Packet" className="max-h-[420px] max-w-full object-contain" /> : null}
      {!src && !failed ? <Loader2 className="h-6 w-6 animate-spin text-muted" aria-label="Loading image" /> : null}
      {failed ? <span className="text-sm text-muted">Image unavailable</span> : null}
    </div>
  );
}

function FindingsPanel({ packet }: { packet: PacketDetail }) {
  const lines = packet.lines ?? [];
  const actionable = lines.filter((line) => line.row_status !== 'blank');
  const actioned = actionable.filter((line) => line.action).length;
  const pct = actionable.length ? Math.round((actioned / actionable.length) * 100) : 0;
  const counts = {
    newSigs: lines.filter((line) => line.row_status === 'new_signature').length,
    already: lines.filter((line) => line.row_status === 'already_counted').length,
    review: lines.filter((line) => line.row_status === 'changed_needs_review').length,
  };

  if (packet.status === 'processing' || packet.status === 'pending') {
    return (
      <aside className="rounded-lg border border-line bg-white p-4">
        <h2 className="section-label">Pre-Review</h2>
        <div className="mt-3 flex items-center gap-2 text-sm text-muted">
          <Loader2 className="h-4 w-4 animate-spin" /> Processing
        </div>
      </aside>
    );
  }

  if (packet.status === 'failed') {
    return (
      <aside className="rounded-lg border border-line bg-white p-4">
        <h2 className="section-label">Pre-Review</h2>
        <p className="mt-3 text-sm text-red-700">{packet.error_msg || 'Processing failed'}</p>
      </aside>
    );
  }

  return (
    <aside className="rounded-lg border border-line bg-white p-4">
      <h2 className="section-label">Signature Count</h2>
      <div className="mt-3 space-y-2 text-sm">
        <div className="flex items-center justify-between">
          <span>New signatures</span>
          <span className="font-semibold text-emerald-700">{counts.newSigs}</span>
        </div>
        <div className="flex items-center justify-between">
          <span>Already counted</span>
          <span className="font-semibold text-muted">{counts.already}</span>
        </div>
        {counts.review ? (
          <div className="flex items-center justify-between">
            <span>Needs review</span>
            <span className="font-semibold text-amber-800">{counts.review}</span>
          </div>
        ) : null}
      </div>
      <div className="my-4 h-px bg-zinc-100" />
      <h2 className="section-label">Review Progress</h2>
      <div className="mt-3 text-sm text-zinc-700">
        {actioned} / {actionable.length} actioned
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded-full bg-zinc-100">
        <div className="h-full rounded-full bg-blue-500 transition-all" style={{ width: `${pct}%` }} />
      </div>
    </aside>
  );
}

function ValidityBar({ lines }: { lines: PacketLine[] }) {
  const active = lines.filter((line) => line.row_status !== 'blank' && line.raw_name);
  const hasVoter = active.some((line) => line.voter_status);
  const hasFraud = active.some((line) => (line.fraud_score ?? 0) > 0);
  if (!active.length || (!hasVoter && !hasFraud)) return null;

  const valid = active.filter((line) => line.voter_status === 'valid' && line.review_decision !== 'confirmed_fraud').length;
  const invalid = active.filter((line) => line.voter_status === 'invalid' || line.review_decision === 'confirmed_fraud').length;
  const uncertain = active.filter((line) => line.voter_status === 'uncertain').length;
  const fraud = active.filter((line) => (line.fraud_score ?? 0) > 30 || line.review_decision === 'confirmed_fraud').length;
  const pct = active.length ? Math.round((valid / active.length) * 100) : 0;

  return (
    <div className="grid gap-2 sm:grid-cols-3 lg:grid-cols-6">
      <Metric label="Total" value={active.length} />
      <Metric label={hasVoter ? 'Valid est.' : 'Clean est.'} value={`${pct}%`} tone="good" />
      <Metric label="Valid" value={valid} tone="good" />
      <Metric label="Invalid" value={invalid} tone="bad" />
      <Metric label="Uncertain" value={uncertain} tone="warn" />
      {hasFraud ? <Metric label="Fraud Risk" value={fraud} tone="bad" /> : null}
    </div>
  );
}

function Metric({ label, value, tone }: { label: string; value: number | string; tone?: 'good' | 'bad' | 'warn' }) {
  const toneClass =
    tone === 'good' ? 'bg-emerald-50 text-emerald-700' : tone === 'bad' ? 'bg-red-50 text-red-800' : tone === 'warn' ? 'bg-amber-50 text-amber-800' : 'bg-zinc-50 text-ink';
  return (
    <div className={`rounded-lg p-3 text-center ${toneClass}`}>
      <div className="text-xl font-bold">{value}</div>
      <div className="mt-1 text-xs text-muted">{label}</div>
    </div>
  );
}

function VoterPanel({ packet }: { packet: PacketDetail }) {
  const queryClient = useQueryClient();
  const [county, setCounty] = useState(packet.county ?? '');
  const [voterRoll, setVoterRoll] = useState(packet.voter_roll_text ?? '');
  const [message, setMessage] = useState('');
  const counties = useQuery({ queryKey: ['counties'], queryFn: api.counties });

  useEffect(() => {
    setCounty(packet.county ?? '');
    setVoterRoll(packet.voter_roll_text ?? '');
    setMessage('');
  }, [packet.id, packet.county, packet.voter_roll_text]);

  const refresh = () => queryClient.invalidateQueries({ queryKey: ['packet', packet.id] });
  const countyMutation = useMutation({
    mutationFn: () => api.setCounty(packet.id, { county }),
    onSuccess: () => {
      setMessage(county ? `Saved ${county} County` : 'County cleared');
      refresh();
    },
  });
  const voterMutation = useMutation({
    mutationFn: () => api.saveVoterRoll(packet.id, { voter_roll_text: voterRoll }),
    onSuccess: (result) => {
      setMessage(`Saved ${result.row_count} voter records`);
      refresh();
    },
  });
  const matchMutation = useMutation({
    mutationFn: () => api.runVoterMatch(packet.id),
    onSuccess: (result) => {
      setMessage(`Matched ${result.matched} rows: ${result.valid} valid, ${result.invalid} invalid, ${result.uncertain} uncertain`);
      refresh();
    },
  });
  const fraudMutation = useMutation({
    mutationFn: () => api.runFraudAnalysis(packet.id),
    onSuccess: (result) => {
      setMessage(`Fraud analysis complete: ${result.flagged} of ${result.lines_analyzed} rows flagged`);
      refresh();
    },
  });

  if (packet.status !== 'done') return null;

  return (
    <section className="space-y-4 rounded-lg border border-line bg-white p-4">
      <h2 className="section-label">Voter Roll & Fraud Review</h2>
      <ValidityBar lines={packet.lines ?? []} />
      <div className="grid gap-3 md:grid-cols-[220px_1fr]">
        <label className="text-sm font-medium text-zinc-700">
          County
          <select
            value={county}
            onChange={(event) => setCounty(event.target.value)}
            className="mt-1 w-full rounded-lg border border-line bg-white px-3 py-2 text-sm outline-none focus:border-blue-500"
          >
            <option value="">No county</option>
            {(counties.data ?? []).map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label className="text-sm font-medium text-zinc-700">
          Voter roll
          <textarea
            value={voterRoll}
            onChange={(event) => setVoterRoll(event.target.value)}
            className="mt-1 h-24 w-full resize-y rounded-lg border border-line px-3 py-2 font-mono text-sm outline-none focus:border-blue-500"
          />
        </label>
      </div>
      <div className="flex flex-wrap gap-2">
        <ToolbarButton icon={<Save />} onClick={() => countyMutation.mutate()} busy={countyMutation.isPending}>
          Save County
        </ToolbarButton>
        <ToolbarButton icon={<Save />} onClick={() => voterMutation.mutate()} busy={voterMutation.isPending} disabled={!voterRoll.trim()}>
          Save Voter Roll
        </ToolbarButton>
        <ToolbarButton icon={<Search />} onClick={() => matchMutation.mutate()} busy={matchMutation.isPending} disabled={!packet.voter_roll_text}>
          Match Voters
        </ToolbarButton>
        <ToolbarButton icon={<ShieldAlert />} onClick={() => fraudMutation.mutate()} busy={fraudMutation.isPending}>
          Analyze Fraud
        </ToolbarButton>
      </div>
      {message ? <p className="text-sm text-emerald-700">{message}</p> : null}
      {[countyMutation, voterMutation, matchMutation, fraudMutation].some((mutation) => mutation.isError) ? (
        <p className="text-sm text-red-700">Request failed</p>
      ) : null}
    </section>
  );
}

function FlaggedQueue({ packet }: { packet: PacketDetail }) {
  const queryClient = useQueryClient();
  const flagged = (packet.lines ?? []).filter((line) => (line.fraud_score ?? 0) > 30 || line.review_decision === 'confirmed_fraud');
  const decisionMutation = useMutation({
    mutationFn: ({ lineNo, decision }: { lineNo: number; decision: Decision }) => api.setDecision(packet.id, lineNo, decision),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['packet', packet.id] }),
  });

  if (!flagged.length) return null;

  return (
    <section className="rounded-lg border border-line bg-white">
      <div className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="section-label">Human Review Queue</h2>
        <span className="text-xs font-semibold text-red-800">{flagged.length} flagged</span>
      </div>
      <div className="overflow-x-auto">
        <table className="review-table">
          <thead>
            <tr>
              <th>Line</th>
              <th>Name</th>
              <th>Address</th>
              <th>Flags</th>
              <th>Score</th>
              <th>Decision</th>
            </tr>
          </thead>
          <tbody>
            {flagged.map((line) => (
              <tr key={line.id} className="bg-red-50/60">
                <td>{line.line_no}</td>
                <td>{line.norm_name || line.raw_name || '-'}</td>
                <td>{line.raw_address || '-'}</td>
                <td>{(line.fraud_flags ?? []).join(', ') || '-'}</td>
                <td className="font-semibold">{line.fraud_score ?? 0}</td>
                <td className="whitespace-nowrap">
                  {line.review_decision ? (
                    <span className={line.review_decision === 'confirmed_fraud' ? 'font-semibold text-red-800' : 'font-semibold text-emerald-700'}>
                      {line.review_decision === 'confirmed_fraud' ? 'Confirmed Fraud' : 'Cleared'}
                    </span>
                  ) : (
                    <div className="flex gap-2">
                      <SmallButton onClick={() => decisionMutation.mutate({ lineNo: line.line_no, decision: 'confirmed_fraud' })} tone="bad">
                        Confirm Fraud
                      </SmallButton>
                      <SmallButton onClick={() => decisionMutation.mutate({ lineNo: line.line_no, decision: 'cleared' })} tone="good">
                        Clear
                      </SmallButton>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function LinesTable({ packet }: { packet: PacketDetail }) {
  const queryClient = useQueryClient();
  const mutation = useMutation({
    mutationFn: ({ lineNo, action }: { lineNo: number; action: Action }) => api.setLineAction(packet.id, lineNo, action),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['packet', packet.id] }),
  });

  if (packet.status !== 'done' || !packet.lines?.length) {
    return <div className="rounded-lg border border-line bg-white p-6 text-center text-sm text-muted">No rows found</div>;
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-white">
      <table className="review-table">
        <thead>
          <tr>
            <th>Line</th>
            <th>Status</th>
            <th>Name</th>
            <th>Address</th>
            <th>City</th>
            <th>ZIP</th>
            <th>Date</th>
            <th>Sig</th>
            <th>Voter</th>
            <th>Fraud</th>
            <th>Flags</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {packet.lines.map((line) => {
            const showAction = line.row_status !== 'blank' && line.row_status !== 'already_counted';
            return (
              <tr key={line.id} className={(line.fraud_score ?? 0) > 0 ? 'bg-red-50/50' : undefined}>
                <td>{line.line_no}</td>
                <td><StatusBadge status={line.row_status} /></td>
                <td>{line.norm_name || line.raw_name || '-'}</td>
                <td className="max-w-[220px] truncate" title={line.norm_address || line.raw_address || ''}>
                  {line.norm_address || line.raw_address || '-'}
                </td>
                <td>{line.raw_city || '-'}</td>
                <td>{line.raw_zip || '-'}</td>
                <td>{line.raw_date || '-'}</td>
                <td>{line.has_signature ? <Check className="h-4 w-4 text-emerald-700" /> : '-'}</td>
                <td>{line.voter_status ? `${line.voter_status} ${line.voter_confidence != null ? `(${line.voter_confidence}%)` : ''}` : '-'}</td>
                <td>{centsLabel(line.fraud_score)}</td>
                <td>{[...(line.fraud_flags ?? []), ...(line.flags ?? [])].join(', ') || '-'}</td>
                <td className="whitespace-nowrap">
                  {line.action ? (
                    <span className={`line-action action-${line.action}`}>{line.action}</span>
                  ) : showAction ? (
                    <div className="flex gap-1">
                      <IconButton label="Approve" onClick={() => mutation.mutate({ lineNo: line.line_no, action: 'approved' })} tone="good">
                        <Check />
                      </IconButton>
                      <IconButton label="Reject" onClick={() => mutation.mutate({ lineNo: line.line_no, action: 'rejected' })} tone="bad">
                        <X />
                      </IconButton>
                      <IconButton label="Escalate" onClick={() => mutation.mutate({ lineNo: line.line_no, action: 'escalated' })} tone="warn">
                        <AlertTriangle />
                      </IconButton>
                    </div>
                  ) : (
                    '-'
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ToolbarButton({
  children,
  icon,
  onClick,
  busy,
  disabled,
}: {
  children: string;
  icon: JSX.Element;
  onClick: () => void;
  busy?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || busy}
      className="inline-flex h-9 items-center gap-2 rounded-lg border border-line bg-white px-3 text-sm font-semibold text-ink hover:border-zinc-400 disabled:cursor-not-allowed disabled:opacity-50"
    >
      {busy ? <Loader2 className="h-4 w-4 animate-spin" /> : <span className="h-4 w-4 [&>svg]:h-4 [&>svg]:w-4">{icon}</span>}
      {children}
    </button>
  );
}

function IconButton({ label, children, onClick, tone }: { label: string; children: JSX.Element; onClick: () => void; tone: 'good' | 'bad' | 'warn' }) {
  const className = tone === 'good' ? 'bg-emerald-100 text-emerald-700' : tone === 'bad' ? 'bg-red-100 text-red-800' : 'bg-amber-100 text-amber-800';
  return (
    <button type="button" aria-label={label} title={label} onClick={onClick} className={`rounded-md p-1.5 ${className}`}>
      <span className="block h-4 w-4 [&>svg]:h-4 [&>svg]:w-4">{children}</span>
    </button>
  );
}

function SmallButton({ children, onClick, tone }: { children: string; onClick: () => void; tone: 'good' | 'bad' }) {
  const className = tone === 'good' ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-800';
  return (
    <button type="button" onClick={onClick} className={`rounded-md px-2 py-1 text-xs font-semibold ${className}`}>
      {children}
    </button>
  );
}

function HeaderActions({ packet }: { packet?: PacketDetail }) {
  const queryClient = useQueryClient();
  const approveAll = useMutation({
    mutationFn: () => {
      if (!packet) throw new Error('No packet selected');
      return api.approveAll(packet.id);
    },
    onSuccess: () => {
      if (packet) queryClient.invalidateQueries({ queryKey: ['packet', packet.id] });
      queryClient.invalidateQueries({ queryKey: ['packets'] });
    },
  });

  const download = async (filter: 'all' | 'valid' | 'flagged') => {
    if (!packet) return;
    const blob = await api.exportBlob(packet.id, filter);
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = `packet_${packet.id}_${filter}.csv`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex flex-wrap justify-end gap-2">
      <ToolbarButton icon={<RefreshCcw />} onClick={() => void queryClient.invalidateQueries()}>
        Refresh
      </ToolbarButton>
      {packet?.status === 'done' ? (
        <>
          <ToolbarButton icon={<Check />} onClick={() => approveAll.mutate()} busy={approveAll.isPending}>
            Approve New Sigs
          </ToolbarButton>
          <ToolbarButton icon={<Download />} onClick={() => void download('all')}>
            Export All
          </ToolbarButton>
          <ToolbarButton icon={<Download />} onClick={() => void download('valid')}>
            Export Valid
          </ToolbarButton>
          <ToolbarButton icon={<Download />} onClick={() => void download('flagged')}>
            Export Flagged
          </ToolbarButton>
        </>
      ) : null}
    </div>
  );
}

export function App() {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const hasSession = api.hasSession();
  const packets = useQuery({
    queryKey: ['packets'],
    queryFn: api.listPackets,
    enabled: hasSession,
  });

  useEffect(() => {
    if (!selectedId && packets.data?.length) {
      setSelectedId(packets.data[0].id);
    }
  }, [packets.data, selectedId]);

  const packet = useQuery({
    queryKey: ['packet', selectedId],
    queryFn: () => api.getPacket(selectedId as number),
    enabled: hasSession && selectedId != null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'processing' || status === 'pending' ? 3000 : false;
    },
  });

  const selectedPacket = packet.data;
  const summary = useMemo(() => {
    const lines = selectedPacket?.lines ?? [];
    return {
      newSigs: lines.filter((line) => line.row_status === 'new_signature').length,
      already: lines.filter((line) => line.row_status === 'already_counted').length,
      review: lines.filter((line) => line.row_status === 'changed_needs_review').length,
    };
  }, [selectedPacket]);

  return (
    <main className="min-h-screen bg-[#f5f5f7] text-ink">
      <header className="border-b border-line bg-white">
        <div className="mx-auto flex max-w-[1500px] flex-col gap-4 px-5 py-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold text-muted">
              <FileText className="h-4 w-4" /> Petition Verifier
            </div>
            <h1 className="mt-1 text-2xl font-bold tracking-normal">Review Queue</h1>
          </div>
          <HeaderActions packet={selectedPacket} />
        </div>
      </header>

      <div className="mx-auto grid max-w-[1500px] gap-4 px-5 py-5 lg:grid-cols-[280px_minmax(0,1fr)]">
        <aside className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="section-label">Packets</h2>
            <span className="text-xs text-muted">{packets.data?.length ?? 0}</span>
          </div>
          {!hasSession ? (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">No active session found.</div>
          ) : null}
          {packets.isLoading ? <div className="rounded-lg border border-line bg-white p-3 text-sm text-muted">Loading packets</div> : null}
          {packets.isError ? <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">Failed to load packets</div> : null}
          {packets.data?.length === 0 ? <div className="rounded-lg border border-line bg-white p-3 text-sm text-muted">No packets yet</div> : null}
          <div className="space-y-2">
            {(packets.data ?? []).map((item) => (
              <PacketCard key={item.id} packet={item} active={item.id === selectedId} onSelect={() => setSelectedId(item.id)} />
            ))}
          </div>
        </aside>

        <section className="min-w-0 space-y-4">
          {packet.isLoading ? <div className="rounded-lg border border-line bg-white p-6 text-sm text-muted">Loading packet</div> : null}
          {packet.isError ? <div className="rounded-lg border border-red-200 bg-red-50 p-6 text-sm text-red-800">Failed to load packet</div> : null}
          {selectedPacket ? (
            <>
              <section className="rounded-lg border border-line bg-white p-4">
                <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                  <div>
                    <h2 className="break-all text-base font-semibold">{selectedPacket.original_name}</h2>
                    <p className="mt-1 text-sm text-muted">
                      {selectedPacket.total_lines ?? 0} rows · {selectedPacket.status}
                      {selectedPacket.error_msg ? ` · ${selectedPacket.error_msg}` : ''}
                    </p>
                    {selectedPacket.status === 'done' ? (
                      <div className="mt-2 flex flex-wrap gap-3 text-sm">
                        <span className="font-semibold text-emerald-700">{summary.newSigs} new</span>
                        <span className="text-muted">{summary.already} already counted</span>
                        {summary.review ? <span className="text-amber-800">{summary.review} needs review</span> : null}
                      </div>
                    ) : null}
                  </div>
                  <span className={`inline-flex rounded-md px-2 py-1 text-xs font-semibold status-${selectedPacket.status}`}>{selectedPacket.status}</span>
                </div>
              </section>

              <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_280px]">
                <ImagePreview packet={selectedPacket} />
                <FindingsPanel packet={selectedPacket} />
              </div>

              <VoterPanel packet={selectedPacket} />
              <FlaggedQueue packet={selectedPacket} />
              <LinesTable packet={selectedPacket} />
            </>
          ) : (
            <div className="rounded-lg border border-line bg-white p-6 text-center text-sm text-muted">Select a packet</div>
          )}
        </section>
      </div>
    </main>
  );
}
