import { useEffect, useState } from 'react';
import { Send, Sparkles } from 'lucide-react';
import { apiGet, apiPost } from '../hooks/useApi';
import type { GenieSpace, GenieSpacesResponse, GenieResult } from '../types';
import Spinner from './Spinner';

// "Test a question" widget shown inside the Genie Spaces pillar when a Genie
// space is configured. Starts a conversation and renders the SQL + result grid.
export default function GenieTester() {
  const [spaces, setSpaces] = useState<GenieSpace[]>([]);
  const [note, setNote] = useState<string | undefined>();
  const [question, setQuestion] = useState('');
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [result, setResult] = useState<GenieResult['result'] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiGet<GenieSpacesResponse>('/genie/spaces')
      .then((r) => {
        setSpaces(r.spaces || []);
        setNote(r.note);
      })
      .catch((e) => setError(e.message));
  }, []);

  async function ask() {
    if (!question.trim() || loading) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      let res: GenieResult;
      if (conversationId) {
        res = await apiPost<GenieResult>('/genie/message', {
          conversation_id: conversationId,
          content: question,
        });
      } else {
        res = await apiPost<GenieResult>('/genie/start-conversation', {
          content: question,
        });
        const cid = (res as unknown as { conversation_id?: string }).conversation_id;
        if (cid) setConversationId(cid);
      }
      setResult(res.result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="rounded-md border border-databricks-200 bg-databricks-50/40 p-3">
      <div className="flex items-center gap-2 mb-2">
        <Sparkles size={15} className="text-databricks-500" />
        <h4 className="text-xs font-semibold uppercase tracking-wider text-databricks-700">
          Test a question
        </h4>
      </div>
      {spaces.length > 0 && (
        <p className="text-[11px] text-ink-400 mb-2">
          Configured space: {spaces.map((s) => s.title).join(', ')}
        </p>
      )}
      {note && <p className="text-[11px] text-ink-400 mb-2">{note}</p>}

      <div className="flex gap-2">
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && ask()}
          placeholder="Ask the Genie space a question..."
          className="flex-1 rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-databricks-300"
        />
        <button onClick={ask} disabled={loading} className="btn-primary py-1.5 px-3 flex items-center gap-1">
          <Send size={14} />
        </button>
      </div>

      {loading && <div className="mt-3"><Spinner label="Asking Genie..." /></div>}
      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      {result && (
        <div className="mt-3 space-y-2">
          {result.text && <p className="text-sm text-ink-700">{result.text}</p>}
          {result.sql && (
            <pre className="bg-ink-900 text-gray-100 rounded-md p-2 overflow-x-auto text-[11px]">
              <code>{result.sql}</code>
            </pre>
          )}
          {result.columns?.length > 0 && (
            <div className="overflow-x-auto rounded-md border border-gray-200">
              <table className="w-full text-xs">
                <thead>
                  <tr className="bg-gray-50">
                    {result.columns.map((c, i) => (
                      <th key={i} className="text-left px-2 py-1 font-semibold text-ink-600 border-b border-gray-200">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {result.rows.slice(0, 20).map((row, ri) => (
                    <tr key={ri} className="border-b border-gray-100">
                      {result.columns.map((c, ci) => (
                        <td key={ci} className="px-2 py-1 text-ink-800 tabular-nums">
                          {String((row as Record<string, unknown>)[c] ?? '')}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
