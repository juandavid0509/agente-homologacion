import { useState, useRef, useEffect, useCallback } from 'react';
import { useStore } from '../store/useStore';
import {
  LogOut, Send, Paperclip, Sparkles, Moon, Sun, Plus, MessageSquare, Loader2,
  Trash2, CheckCircle2, Lock
} from 'lucide-react';
import api from '../services/api';

type Role = 'user' | 'assistant' | 'system' | 'tool';

interface ChatMessage {
  id?: number;
  role: Role;
  content: string;
}

interface Conversation {
  id: number;
  title: string;
  state: string;
  proceso_completado?: boolean;
  locked?: boolean;
  updated_at?: string;
}

const escapeHtml = (text: string) =>
  text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');

const inlineFormat = (text: string) =>
  escapeHtml(text)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>');

// Render seguro y legible: títulos, viñetas, listas numeradas y negritas.
const FormattedText = ({ text }: { text: string }) => {
  const html = text
    .split('\n')
    .map((raw) => {
      const line = raw.trim();
      if (!line) return '<div class="h-2"></div>';

      const heading = line.match(/^(#{1,3})\s+(.*)$/);
      if (heading) {
        const size = heading[1].length === 1 ? 'text-lg' : 'text-base';
        return `<div class="font-bold ${size} mt-2 mb-1">${inlineFormat(heading[2])}</div>`;
      }

      if (/^[•\-]\s+/.test(line)) {
        return `<div class="flex gap-2"><span class="select-none">•</span><span>${inlineFormat(
          line.replace(/^[•\-]\s+/, '')
        )}</span></div>`;
      }

      if (/^\d+\.\s+/.test(line)) {
        return `<div class="pl-1 mt-1">${inlineFormat(line)}</div>`;
      }

      return `<div class="mt-1">${inlineFormat(line)}</div>`;
    })
    .join('');

  return <div className="leading-relaxed" dangerouslySetInnerHTML={{ __html: html }} />;
};

export default function Dashboard() {
  const { user, setUser, toggleDarkMode, darkMode } = useStore();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [input, setInput] = useState('');
  const [isTyping, setIsTyping] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Mantiene el cursor en el campo de escritura tras enviar o cargar un chat.
  const focusInput = useCallback(() => {
    requestAnimationFrame(() => inputRef.current?.focus());
  }, []);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isTyping]);

  const loadMessages = useCallback(async (convId: number) => {
    if (!user) return;
    const res = await api.get(`/conversations/${convId}/messages`, {
      params: { user_id: user.id },
    });
    setMessages(
      (res.data as { role: Role; content: string; id: number }[]).map((m) => ({
        id: m.id,
        role: m.role,
        content: m.content,
      }))
    );
  }, [user]);

  const selectConversation = useCallback(async (convId: number) => {
    setConversationId(convId);
    setError(null);
    setSavedNotice(null);
    try {
      await loadMessages(convId);
    } catch {
      setError('No se pudo cargar el historial de esta conversación.');
    }
  }, [loadMessages]);

  // Cargar historial de trámites terminados y el borrador en curso.
  useEffect(() => {
    if (!user) return;
    let active = true;

    const init = async () => {
      setIsLoadingHistory(true);
      try {
        const [listRes, draftRes] = await Promise.all([
          api.get('/conversations', { params: { user_id: user.id } }),
          api.get('/conversations/draft', { params: { user_id: user.id } }),
        ]);

        if (!active) return;
        setConversations(listRes.data);

        const draft = draftRes.data as Conversation | null;
        if (draft?.id) {
          setConversationId(draft.id);
          await loadMessages(draft.id);
        } else if (listRes.data.length > 0) {
          const latest = listRes.data[0] as Conversation;
          setConversationId(latest.id);
          await loadMessages(latest.id);
        } else {
          setConversationId(null);
          setMessages([]);
        }
      } catch {
        if (active) setError('No se pudo conectar con el servidor. Verifica que el backend esté encendido.');
      } finally {
        if (active) setIsLoadingHistory(false);
      }
    };

    init();
    return () => { active = false; };
  }, [user, loadMessages]);

  const refreshConversations = async (selectId?: number) => {
    if (!user) return;
    try {
      const res = await api.get('/conversations', { params: { user_id: user.id } });
      setConversations(res.data);
      if (selectId) await selectConversation(selectId);
    } catch {
      /* silencioso: no bloquear el chat */
    }
  };

  const newConversation = async () => {
    if (!user || isTyping) return;
    setSavedNotice(null);
    setError(null);
    try {
      const res = await api.post('/conversations', { user_id: user.id });
      setConversationId(res.data.id);
      await loadMessages(res.data.id);
    } catch {
      setError('No se pudo crear una conversación nueva.');
    }
  };

  const deleteConversation = async (id: number) => {
    if (!user) return;
    if (!window.confirm('¿Eliminar este chat? Esta acción no se puede deshacer.')) return;
    try {
      await api.delete(`/conversations/${id}`, { params: { user_id: user.id } });
      setConversations((prev) => prev.filter((c) => c.id !== id));
      if (conversationId === id) {
        setConversationId(null);
        setMessages([]);
        setSavedNotice(null);
      }
    } catch {
      setError('No se pudo eliminar el chat. Inténtalo de nuevo.');
    }
  };

  const sendMessage = async () => {
    if (!input.trim() || isTyping || !user) return;

    const text = input;
    setInput('');
    setError(null);
    setMessages((prev) => [...prev, { role: 'user', content: text }]);
    setIsTyping(true);

    try {
      const res = await api.post('/chat', {
        message: text,
        user_id: user.id,
        conversation_id: conversationId,
      });

      // Si el backend creó una conversación, la adoptamos.
      if (!conversationId && res.data.conversation_id) {
        setConversationId(res.data.conversation_id);
      }

      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: res.data.response },
      ]);

      if (res.data.conversation_saved) {
        setSavedNotice('✅ Trámite completado y guardado en tu historial.');
      }
      refreshConversations();
    } catch (e: any) {
      const detail = e.response?.data?.detail;
      const msg = typeof detail === 'string' ? detail : null;
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content:
            msg ||
            '❌ Lo siento, tengo problemas para conectarme con el servidor. Verifica que el backend esté encendido.',
        },
      ]);
    } finally {
      setIsTyping(false);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file || !user) return;
    if (file.type !== 'application/pdf') {
      alert('Solo se permiten PDFs');
      return;
    }
    if (file.size > 5 * 1024 * 1024) {
      alert('El archivo supera el tamaño máximo permitido de 5 MB');
      if (fileInputRef.current) fileInputRef.current.value = '';
      return;
    }

    const formData = new FormData();
    formData.append('file', file);
    formData.append('user_id', user.id.toString());
    if (conversationId) formData.append('conversation_id', conversationId.toString());
    formData.append('tipo_documento', '');

    setIsUploading(true);
    setMessages((prev) => [
      ...prev,
      { role: 'user', content: `📎 [Subiendo archivo: ${file.name}]` },
    ]);

    try {
      const res = await api.post('/upload_document', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      if (!conversationId && res.data.conversation_id) {
        setConversationId(res.data.conversation_id);
      }
      setMessages((prev) => [
        ...prev,
        { role: 'assistant', content: res.data.response },
      ]);
      if (res.data.conversation_saved) {
        setSavedNotice('✅ Trámite completado y guardado en tu historial.');
      }
      refreshConversations();
    } catch (e: any) {
      const detail = e.response?.data?.detail;
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: typeof detail === 'string' ? detail : 'Hubo un error subiendo el archivo.',
        },
      ]);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const firstName = user?.name?.split(' ')[0] || 'estudiante';

  return (
    <div className="flex h-screen bg-[#F3F4F6] dark:bg-gray-900 font-sans">
      <aside className="w-72 bg-white dark:bg-gray-800 border-r border-gray-100 dark:border-gray-700 flex flex-col shadow-sm z-10">
        <div className="p-6 flex items-center gap-3 border-b border-gray-100 dark:border-gray-700">
          <div className="w-10 h-10 bg-gradient-to-tr from-blue-600 to-indigo-500 rounded-xl flex items-center justify-center text-white font-bold shadow-lg shadow-blue-500/30">
            U
          </div>
          <span className="font-bold text-xl text-gray-800 dark:text-white tracking-tight">UniEspinal</span>
        </div>

        <div className="p-4 flex-1 overflow-y-auto">
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-xs font-bold text-gray-400 uppercase tracking-wider">Conversaciones</h3>
            <button
              onClick={newConversation}
              disabled={isTyping}
              title="Nueva conversación"
              className="p-1.5 text-blue-600 hover:bg-blue-50 dark:hover:bg-gray-700 rounded-lg disabled:opacity-40"
            >
              <Plus size={18} />
            </button>
          </div>

          {conversations.length === 0 && (
            <p className="text-sm text-gray-400 leading-relaxed">
              Aún no hay trámites guardados. Cuando completes una homologación,
              el chat aparecerá aquí.
            </p>
          )}

          <ul className="space-y-2">
            {conversations.map((conv) => (
              <li key={conv.id} className="group flex items-start gap-1">
                <button
                  onClick={() => selectConversation(conv.id)}
                  className={`flex-1 min-w-0 text-left p-3 rounded-2xl flex items-start gap-3 text-sm transition-all border ${
                    conv.id === conversationId
                      ? 'bg-blue-50 dark:bg-gray-700/50 text-blue-700 dark:text-blue-300 border-blue-100 dark:border-gray-600 shadow-sm'
                      : 'text-gray-600 dark:text-gray-300 border-transparent hover:bg-gray-50 dark:hover:bg-gray-700/40'
                  }`}
                >
                  <MessageSquare size={18} className="mt-0.5 shrink-0" />
                  <span className="line-clamp-2 break-words">{conv.title}</span>
                  <CheckCircle2 size={14} className="mt-1 shrink-0 text-emerald-500" />
                </button>
                <button
                  onClick={() => deleteConversation(conv.id)}
                  title="Eliminar chat"
                  aria-label="Eliminar chat"
                  className="mt-1 p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity"
                >
                  <Trash2 size={16} />
                </button>
              </li>
            ))}
          </ul>
        </div>

        <div className="p-4 border-t border-gray-100 dark:border-gray-700 space-y-4">
          <div className="flex items-center justify-between px-3 py-2">
            <div className="flex items-center gap-3 text-sm font-medium text-gray-600 dark:text-gray-300">
              {darkMode ? <Moon size={18} /> : <Sun size={18} />}
              <span>Modo Oscuro</span>
            </div>
            <button
              onClick={toggleDarkMode}
              className={`w-12 h-6 rounded-full p-1 transition-colors duration-300 ease-in-out ${darkMode ? 'bg-blue-600' : 'bg-gray-300'}`}
            >
              <div className={`bg-white w-4 h-4 rounded-full shadow-md transform transition-transform duration-300 ease-in-out ${darkMode ? 'translate-x-6' : 'translate-x-0'}`} />
            </button>
          </div>
          <button
            onClick={() => setUser(null)}
            className="flex items-center gap-3 text-sm font-medium w-full p-3 text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-2xl transition-all"
          >
            <LogOut size={18} /> Cerrar Sesión
          </button>
        </div>
      </aside>

      <main className="flex-1 flex flex-col relative">
        <header className="h-20 flex items-center justify-between px-8 bg-white/80 dark:bg-gray-800/80 backdrop-blur-md border-b border-gray-100 dark:border-gray-700 absolute top-0 w-full z-10">
          <div className="flex items-center gap-2">
            <Sparkles className="text-blue-500" size={20} />
            <h1 className="font-bold text-lg text-gray-800 dark:text-white">Chat con Lía</h1>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-3 bg-white dark:bg-gray-700 px-4 py-2 rounded-full shadow-sm border border-gray-100 dark:border-gray-600">
              <span className="text-sm font-semibold text-gray-700 dark:text-gray-200">{user?.name}</span>
              <div className="w-8 h-8 bg-gradient-to-tr from-blue-600 to-indigo-500 text-white rounded-full flex items-center justify-center uppercase font-bold text-xs shadow-md">
                {user?.name?.[0]}
              </div>
            </div>
            {conversationId && (
              <button
                onClick={() => deleteConversation(conversationId)}
                title="Eliminar chat actual"
                aria-label="Eliminar chat actual"
                className="p-2 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-full transition-colors"
              >
                <Trash2 size={18} />
              </button>
            )}
          </div>
        </header>

        <div className="flex-1 overflow-y-auto pt-24 pb-32 px-6 space-y-6 scroll-smooth">
          {isLoadingHistory && (
            <div className="flex justify-center text-gray-400 pt-10 gap-2 items-center text-sm">
              <Loader2 className="animate-spin" size={18} /> Cargando conversación…
            </div>
          )}

          {error && (
            <div className="max-w-3xl mx-auto bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-300 border border-red-100 dark:border-red-800 rounded-2xl p-4 text-sm">
              {error}
            </div>
          )}

          {savedNotice && (
            <div className="max-w-3xl mx-auto bg-emerald-50 dark:bg-emerald-900/20 text-emerald-700 dark:text-emerald-300 border border-emerald-100 dark:border-emerald-800 rounded-2xl p-4 text-sm flex items-center gap-2">
              <CheckCircle2 size={18} /> {savedNotice}
            </div>
          )}

          {!isLoadingHistory && messages.length === 0 && (
            <div className="flex justify-start">
              <div className="max-w-[70%] p-5 text-[15px] leading-relaxed shadow-sm bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 border border-gray-100 dark:border-gray-700 rounded-3xl rounded-bl-sm">
                <FormattedText
                  text={`¡Hola, ${firstName}! 😊 Soy **Lía**, tu orientadora en procesos de homologación. Puedo explicarte los requisitos, revisar si una materia figura como homologable o acompañarte paso a paso en tu solicitud.\n\nSi quieres, dime **«toda la información»** y te muestro el panorama completo. ¿En qué te ayudo hoy?`}
                />
              </div>
            </div>
          )}

          {messages.map((m, i) => (
            <div key={m.id ?? i} className={`flex ${m.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div
                className={`max-w-[70%] p-5 text-[15px] leading-relaxed shadow-sm ${
                  m.role === 'user'
                    ? 'bg-gradient-to-br from-blue-600 to-indigo-600 text-white rounded-3xl rounded-br-sm shadow-blue-500/20'
                    : 'bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-100 border border-gray-100 dark:border-gray-700 rounded-3xl rounded-bl-sm'
                }`}
              >
                <FormattedText text={m.content} />
              </div>
            </div>
          ))}

          {isTyping && (
            <div className="flex justify-start">
              <div className="bg-white dark:bg-gray-800 border border-gray-100 dark:border-gray-700 rounded-3xl rounded-bl-sm p-5 shadow-sm flex items-center gap-1">
                <div className="w-2 h-2 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                <div className="w-2 h-2 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                <div className="w-2 h-2 bg-blue-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
            </div>
          )}

          <div ref={chatEndRef} />
        </div>

        <div className="absolute bottom-0 w-full p-6 bg-gradient-to-t from-[#F3F4F6] via-[#F3F4F6] dark:from-gray-900 dark:via-gray-900 to-transparent">
          <div className="max-w-4xl mx-auto flex items-center gap-3 bg-white dark:bg-gray-800 p-2 pl-4 rounded-full shadow-xl shadow-gray-200/50 dark:shadow-black/30 border border-gray-100 dark:border-gray-700 transition-all focus-within:ring-2 focus-within:ring-blue-500/50">
            <input type="file" accept=".pdf" ref={fileInputRef} onChange={handleFileUpload} className="hidden" />
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={isUploading || isTyping}
              className="p-2 text-gray-400 hover:text-blue-500 transition-colors rounded-full hover:bg-blue-50 dark:hover:bg-gray-700"
            >
              <Paperclip size={22} className={isUploading ? 'animate-pulse text-blue-500' : ''} />
            </button>

            <input
              type="text"
              placeholder={`Escríbele a Lía, ${firstName}…`}
              className="flex-1 bg-transparent outline-none text-[15px] text-gray-700 dark:text-gray-200 placeholder-gray-400"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && sendMessage()}
              disabled={isTyping || isLoadingHistory}
            />
            <button
              onClick={sendMessage}
              disabled={isTyping || isLoadingHistory}
              className="bg-gradient-to-tr from-blue-600 to-indigo-500 text-white p-3 rounded-full hover:opacity-90 transition-all shadow-lg shadow-blue-500/40 disabled:opacity-50"
            >
              <Send size={20} className="ml-1" />
            </button>
          </div>
        </div>
      </main>
    </div>
  );
}
