import { useState, useEffect, useMemo } from 'react';
import { createClient, type SupabaseClient } from '@supabase/supabase-js';

import { UploadZone } from './components/UploadZone';
import { MindmapCanvas, type MindmapNode } from './components/MindmapCanvas';
import { MathRenderer } from './components/MathRenderer';
import { useToast } from './components/Toast';
import { 
  Maximize2, 
  Minimize2, 
  Trash2, 
  RotateCcw, 
  X, 
  BookOpen, 
  Cpu, 
  Undo2
} from 'lucide-react';


// Document storage model
export interface SavedDocument {
  id: string;
  name: string;
  data: MindmapNode;
  userEmail?: string;
}

const API_BASE = import.meta.env.VITE_API_BASE_URL 
  ? `${import.meta.env.VITE_API_BASE_URL}/api` 
  : (typeof window !== 'undefined' && window.location.hostname !== 'localhost'
      ? '/api'
      : 'http://localhost:8000/api');

export default function App() {
  const toast = useToast();
  // Supabase Client state
  const [supabase, setSupabase] = useState<SupabaseClient | null>(null);

  // Authentication states
  const [currentUserEmail, setCurrentUserEmail] = useState<string | null>(() => {
    return localStorage.getItem('pdf_mindmaps_user') || null;
  });
  const [authMode, setAuthMode] = useState<'signin' | 'signup' | 'forgot_password' | 'reset_password'>('signin');
  const [authEmail, setAuthEmail] = useState('');
  const [authPassword, setAuthPassword] = useState('');
  const [authConfirmPassword, setAuthConfirmPassword] = useState('');
  const [authError, setAuthError] = useState('');
  const [authSuccessMessage, setAuthSuccessMessage] = useState('');
  const [authLoading, setAuthLoading] = useState(false);

  // Clean up URL pathname on mount if it's '/reset-password'
  useEffect(() => {
    if (window.location.pathname === '/reset-password') {
      window.history.replaceState({}, document.title, '/');
    }
  }, []);

  // Fetch Supabase configuration and instantiate client on startup
  useEffect(() => {
    const initSupabase = async () => {
      try {
        const apiBaseUrl = import.meta.env.VITE_API_BASE_URL 
          || (typeof window !== 'undefined' && window.location.hostname !== 'localhost' ? '' : 'http://localhost:8000');
        const response = await fetch(`${apiBaseUrl}/api/auth/config`);
        if (response.ok) {
          const config = await response.json();
          const client = createClient(config.supabaseUrl, config.supabaseKey);
          setSupabase(client);
        } else {
          console.error("Failed to load Supabase config from API");
          toast.warning("Could not load database configuration. Working in local-only offline mode.");
        }
      } catch (err) {
        console.error("Error loading Supabase config:", err);
        toast.warning("Could not connect to database server. Working in local-only offline mode.");
      }
    };
    initSupabase();
  }, []);

  // Setup Supabase authentication event listeners
  useEffect(() => {
    if (!supabase) return;

    // Sync session if present, but do not override local user state if session is absent
    supabase.auth.getSession().then(({ data: { session } }) => {
      if (session?.user?.email) {
        setCurrentUserEmail(session.user.email);
        localStorage.setItem('pdf_mindmaps_user', session.user.email);
      }
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange(async (event, session) => {
      if (session?.user?.email) {
        setCurrentUserEmail(session.user.email);
        localStorage.setItem('pdf_mindmaps_user', session.user.email);
      }

      if (event === 'PASSWORD_RECOVERY') {
        setAuthMode('reset_password');
      }
    });

    return () => {
      subscription.unsubscribe();
    };
  }, [supabase]);

  // Documents state - loaded from REST API or local storage
  const [documents, setDocuments] = useState<SavedDocument[]>(() => {
    const saved = localStorage.getItem('pdf_mindmaps_docs');
    return saved ? JSON.parse(saved) : [];
  });

  const [activeDocId, setActiveDocId] = useState<string | null>(() => {
    return localStorage.getItem('pdf_mindmaps_active_id') || null;
  });

  const [selectedNode, setSelectedNode] = useState<MindmapNode | null>(null);

  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => {
    const saved = localStorage.getItem('pdf_mindmaps_expanded');
    return saved ? new Set(JSON.parse(saved)) : new Set(['root']);
  });

  const [isFocusMode, setIsFocusMode] = useState<boolean>(() => {

    return localStorage.getItem('pdf_mindmaps_focus_mode') === 'true';
  });

  const [selectedModel, setSelectedModel] = useState<string>(() => {
    const saved = localStorage.getItem('pdf_mindmaps_model');
    const validModels = [
      'auto-smart-routing',
      'llama-3.3-70b-versatile',
      'deepseek-r1-distill-llama-70b',
      'mixtral-8x7b-32768',
      'gemma2-9b-it',
      'llama3-70b-8192',
      'llama3-8b-8192'
    ];
    return (saved && validModels.includes(saved)) ? saved : 'auto-smart-routing';
  });

  const [selectedSubject, setSelectedSubject] = useState<string>(() => {
    const saved = localStorage.getItem('pdf_mindmaps_subject');
    const validSubjects = ['general', 'math', 'physics', 'geography', 'history'];
    return (saved && validSubjects.includes(saved)) ? saved : 'general';
  });


  // Undo history stack for expanded node states
  const [undoHistory, setUndoHistory] = useState<string[][]>([]);

  // Filter documents: Show user's documents if logged in, or all local documents
  const userDocuments = useMemo(() => {
    return documents.filter(doc => !currentUserEmail || !doc.userEmail || doc.userEmail === currentUserEmail);
  }, [documents, currentUserEmail]);

  // Find the active document with fallback to first document
  const activeDoc = useMemo(() => {
    return userDocuments.find(doc => doc.id === activeDocId) || (userDocuments.length > 0 ? userDocuments[0] : null);
  }, [userDocuments, activeDocId]);

  // Ensure activeDocId stays in sync if activeDoc fallback was chosen
  useEffect(() => {
    if (activeDoc && activeDoc.id !== activeDocId) {
      setActiveDocId(activeDoc.id);
    }
  }, [activeDoc, activeDocId]);

  // Persist state updates to localStorage

  useEffect(() => {
    if (documents.length > 0) {
      localStorage.setItem('pdf_mindmaps_docs', JSON.stringify(documents));
    }
  }, [documents]);

  useEffect(() => {
    if (activeDocId) {
      localStorage.setItem('pdf_mindmaps_active_id', activeDocId);
    } else {
      localStorage.removeItem('pdf_mindmaps_active_id');
    }
  }, [activeDocId]);

  useEffect(() => {
    localStorage.setItem('pdf_mindmaps_expanded', JSON.stringify(Array.from(expandedIds)));
  }, [expandedIds]);

  useEffect(() => {
    localStorage.setItem('pdf_mindmaps_focus_mode', String(isFocusMode));
  }, [isFocusMode]);

  useEffect(() => {
    localStorage.setItem('pdf_mindmaps_model', selectedModel);
  }, [selectedModel]);

  useEffect(() => {
    localStorage.setItem('pdf_mindmaps_subject', selectedSubject);
  }, [selectedSubject]);

  // Hydrate user documents from database gracefully without wiping local documents
  useEffect(() => {
    if (!currentUserEmail) return;

    const fetchUserDocuments = async () => {
      try {
        const response = await fetch(`${API_BASE}/documents?email=${encodeURIComponent(currentUserEmail)}`);
        if (response.ok) {
          const docs = await response.json();
          if (Array.isArray(docs) && docs.length > 0) {
            setDocuments(prev => {
              // Merge remote and local documents uniquely by ID
              const existingIds = new Set(docs.map(d => d.id));
              const localOnly = prev.filter(d => !existingIds.has(d.id));
              return [...docs, ...localOnly];
            });
          }
        }
      } catch (err) {
        console.warn("Database sync offline, using local cached documents:", err);
      }
    };

    fetchUserDocuments();
  }, [currentUserEmail]);


  // Handle Authentication Submission
  const handleAuthSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setAuthError('');
    setAuthSuccessMessage('');
    setAuthLoading(true);

    const email = authEmail.trim().toLowerCase();

    try {
      if (authMode === 'signin') {
        const response = await fetch(`${API_BASE}/auth/signin`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, password: authPassword }),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || 'Invalid email or password.');
        }
        
        setCurrentUserEmail(email);
        localStorage.setItem('pdf_mindmaps_user', email);
        toast.success(`Welcome back, ${email}!`);
        
        if (supabase) {
          supabase.auth.signInWithPassword({ email, password: authPassword }).catch(() => {});
        }

        setAuthEmail('');
        setAuthPassword('');
      } else if (authMode === 'signup') {
        if (authPassword !== authConfirmPassword) {
          throw new Error('Passwords do not match.');
        }

        const response = await fetch(`${API_BASE}/auth/signup`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, password: authPassword }),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || 'Sign up failed.');
        }

        if (supabase) {
          supabase.auth.signUp({ email, password: authPassword }).catch(() => {});
        }

        setAuthSuccessMessage('Account created successfully! Please sign in.');
        setAuthMode('signin');
        setAuthPassword('');
        setAuthConfirmPassword('');
        toast.success('Account created successfully!');
      } else if (authMode === 'forgot_password') {
        const response = await fetch(`${API_BASE}/auth/forgot-password`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email }),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || 'Failed to send reset email.');
        }

        if (supabase) {
          supabase.auth.resetPasswordForEmail(email, {
            redirectTo: `${window.location.origin}/reset-password`,
          }).catch(() => {});
        }

        setAuthSuccessMessage('If the email is registered, password reset instructions have been sent.');
        toast.info('Password reset instructions sent.');
      } else if (authMode === 'reset_password') {
        if (authPassword !== authConfirmPassword) {
          throw new Error('Passwords do not match.');
        }

        const response = await fetch(`${API_BASE}/auth/reset-password`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email, newPassword: authPassword }),
        });
        const data = await response.json();
        if (!response.ok) {
          throw new Error(data.detail || 'Failed to reset password.');
        }

        if (supabase) {
          supabase.auth.updateUser({ password: authPassword }).catch(() => {});
        }

        setAuthSuccessMessage('Password reset successfully! Please sign in with your new password.');
        setAuthMode('signin');
        setAuthPassword('');
        setAuthConfirmPassword('');
        toast.success('Password updated successfully!');
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'An error occurred during authentication.';
      setAuthError(msg);
    } finally {
      setAuthLoading(false);
    }
  };


  // Handle Sign Out
  const handleSignOut = async () => {
    if (supabase) {
      await supabase.auth.signOut();
    }
    setCurrentUserEmail(null);
    localStorage.removeItem('pdf_mindmaps_user');
    setSelectedNode(null);
    setUndoHistory([]);
  };

  // Handle new mindmap generation
  const handleMindmapGenerated = async (filename: string, mindmapData: MindmapNode) => {
    const newDocId = crypto.randomUUID ? crypto.randomUUID() : Date.now().toString();
    const newDoc: SavedDocument = {
      id: newDocId,
      name: filename,
      data: mindmapData,
      userEmail: currentUserEmail || undefined
    };


    // Update local state for instant user feedback
    setDocuments(prev => [newDoc, ...prev]);
    setActiveDocId(newDoc.id);

    // Auto-expand all nodes across the canvas so the user immediately sees the entire mindmap
    const allNodeIds = new Set<string>();
    const collectIds = (n: MindmapNode) => {
      if (n.id) allNodeIds.add(n.id);
      if (n.children && Array.isArray(n.children)) {
        n.children.forEach(collectIds);
      }
    };
    collectIds(mindmapData);

    setExpandedIds(allNodeIds);
    setSelectedNode(mindmapData); // Auto-open Details Panel for the root node
    setUndoHistory([]);

    // Background sync to database
    if (currentUserEmail) {
      try {
        const response = await fetch(`${API_BASE}/documents`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            id: newDoc.id,
            name: newDoc.name,
            data: newDoc.data,
            userEmail: currentUserEmail
          }),
        });
        if (!response.ok) {
          throw new Error("Failed to save to database");
        }
        toast.success("Mindmap generated and synced to cloud.");
      } catch (err) {
        console.error("Error syncing new document to database:", err);
        toast.info("Mindmap created and saved locally in browser.");
      }
    } else {
      toast.success("Mindmap generated successfully.");
    }
  };

  // Switch between documents
  const handleSelectDocument = (docId: string) => {
    setActiveDocId(docId);
    const targetDoc = userDocuments.find(d => d.id === docId);
    if (targetDoc && targetDoc.data) {
      const allIds = new Set<string>();
      const collect = (n: MindmapNode) => {
        if (n.id) allIds.add(n.id);
        if (n.children && Array.isArray(n.children)) {
          n.children.forEach(collect);
        }
      };
      collect(targetDoc.data);
      setExpandedIds(allIds);
      setSelectedNode(targetDoc.data);
    } else {
      setExpandedIds(new Set(['root']));
      setSelectedNode(null);
    }
    setUndoHistory([]);
  };


  // Delete a document
  const handleDeleteDocument = async (docId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setDocuments(prev => prev.filter(doc => doc.id !== docId));
    if (activeDocId === docId) {
      const remaining = userDocuments.filter(doc => doc.id !== docId);
      setActiveDocId(remaining.length > 0 ? remaining[0].id : null);
      setSelectedNode(null);
      setUndoHistory([]);
    }

    // Async database sync
    if (currentUserEmail) {
      try {
        const response = await fetch(`${API_BASE}/documents/${docId}?email=${encodeURIComponent(currentUserEmail)}`, {
          method: 'DELETE',
        });
        if (!response.ok) {
          throw new Error("Failed to delete from database");
        }
        toast.success("Document deleted.");
      } catch (err) {
        console.error("Error syncing deletion to database:", err);
        toast.warning("Failed to delete document from cloud. It has been removed locally.");
      }
    } else {
      toast.success("Document deleted.");
    }
  };

  // Toggle node expansion state (progressive disclosure)
  const handleToggleNodeExpand = (nodeId: string) => {
    // Save current expanded state to history stack for Undo
    setUndoHistory(prev => [...prev, Array.from(expandedIds)]);

    setExpandedIds(prev => {
      const next = new Set(prev);
      if (next.has(nodeId)) {
        next.delete(nodeId);
      } else {
        next.add(nodeId);
      }
      return next;
    });
  };

  // Trigger undo of node expansion actions
  const handleUndo = () => {
    if (undoHistory.length === 0) return;
    
    setUndoHistory(prev => {
      const nextHistory = [...prev];
      const previousState = nextHistory.pop();
      if (previousState) {
        setExpandedIds(new Set(previousState));
      }
      return nextHistory;
    });
  };

  // Reset workspace
  const handleResetAll = async () => {
    if (window.confirm("Are you sure you want to reset the workspace? This will clear all uploaded documents and history from the database.")) {
      setDocuments([]);
      setActiveDocId(null);
      setExpandedIds(new Set(['root']));
      setSelectedNode(null);
      setUndoHistory([]);
      
      const emailCache = currentUserEmail;
      localStorage.clear();
      // Re-initialize default settings
      localStorage.setItem('pdf_mindmaps_focus_mode', 'false');
      localStorage.setItem('pdf_mindmaps_model', 'llama-3.3-70b-versatile');
      if (emailCache) {
        localStorage.setItem('pdf_mindmaps_user', emailCache); // Keep user authenticated
      }

      // Async database reset
      if (emailCache) {
        try {
          const response = await fetch(`${API_BASE}/documents/reset/workspace?email=${encodeURIComponent(emailCache)}`, {
            method: 'DELETE',
          });
          if (!response.ok) {
            throw new Error("Failed to reset workspace in database");
          }
          toast.success("Workspace reset successfully.");
        } catch (err) {
          console.error("Error resetting workspace in database:", err);
          toast.warning("Local workspace cleared, but failed to sync reset to cloud.");
        }
      } else {
        toast.success("Workspace reset successfully.");
      }
    }
  };

  // Select node to show details panel or null to close
  const handleSelectNode = (node: MindmapNode | null) => {
    setSelectedNode(node);
  };



  // If not authenticated, render Login / Signup card
  if (!currentUserEmail) {
    const getTitle = () => {
      switch (authMode) {
        case 'signin': return 'Sign In';
        case 'signup': return 'Create Account';
        case 'forgot_password': return 'Forgot Password';
        case 'reset_password': return 'Reset Password';
      }
    };

    return (
      <div className="flex h-screen w-screen items-center justify-center bg-slate-50 font-sans select-none">
        <div className="w-[380px] bg-white border border-slate-200 p-6 flex flex-col">
          {/* Logo / Title */}
          <div className="flex items-center gap-2 mb-6">
            <BookOpen className="w-6 h-6 text-blue-500 stroke-[1.5]" />
            <h1 className="text-sm font-bold text-slate-800 tracking-tight">
              PDF-to-Mindmap
            </h1>
          </div>
          
          <h2 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-4">
            {getTitle()}
          </h2>
          
          {authError && (
            <div className="mb-4 p-3 bg-rose-50 border border-rose-100 text-rose-600 text-xs font-semibold leading-normal">
              {authError}
            </div>
          )}

          {authSuccessMessage && (
            <div className="mb-4 p-3 bg-blue-50 border border-blue-100 text-blue-600 text-xs font-semibold leading-normal">
              {authSuccessMessage}
            </div>
          )}

          <form onSubmit={handleAuthSubmit} className="space-y-4">
            {authMode !== 'reset_password' && (
              <div>
                <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block mb-1.5">
                  Email Address
                </label>
                <input
                  type="email"
                  required
                  value={authEmail}
                  onChange={(e) => setAuthEmail(e.target.value)}
                  placeholder="user@example.com"
                  className="w-full text-xs bg-slate-50 border border-slate-200 text-slate-700 px-2.5 py-2 focus:outline-none focus:border-slate-300 font-medium rounded-none"
                  disabled={authLoading}
                />
              </div>
            )}
            
            {authMode !== 'forgot_password' && (
              <div>
                <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block mb-1.5">
                  {authMode === 'reset_password' ? 'New Password' : 'Password'}
                </label>
                <input
                  type="password"
                  required
                  value={authPassword}
                  onChange={(e) => setAuthPassword(e.target.value)}
                  placeholder="••••••••"
                  className="w-full text-xs bg-slate-50 border border-slate-200 text-slate-700 px-2.5 py-2 focus:outline-none focus:border-slate-300 font-medium rounded-none"
                  disabled={authLoading}
                />
              </div>
            )}

            {authMode === 'reset_password' && (
              <div>
                <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block mb-1.5">
                  Confirm New Password
                </label>
                <input
                  type="password"
                  required
                  value={authConfirmPassword}
                  onChange={(e) => setAuthConfirmPassword(e.target.value)}
                  placeholder="••••••••"
                  className="w-full text-xs bg-slate-50 border border-slate-200 text-slate-700 px-2.5 py-2 focus:outline-none focus:border-slate-300 font-medium rounded-none"
                  disabled={authLoading}
                />
              </div>
            )}

            {authMode === 'signin' && (
              <div className="text-right">
                <button
                  type="button"
                  onClick={() => {
                    setAuthMode('forgot_password');
                    setAuthError('');
                    setAuthSuccessMessage('');
                    setAuthPassword('');
                  }}
                  className="text-[11px] text-blue-500 hover:text-blue-600 font-medium bg-transparent border-0 cursor-pointer focus:outline-none"
                >
                  Forgot Password?
                </button>
              </div>
            )}
            
            <button
              type="submit"
              disabled={authLoading}
              className="w-full bg-slate-800 hover:bg-slate-900 text-white text-xs font-semibold py-2 px-4 transition-none cursor-pointer focus:outline-none disabled:opacity-50 disabled:cursor-not-allowed rounded-none text-center"
            >
              {authLoading 
                ? 'Processing...' 
                : authMode === 'signin' 
                  ? 'Sign In' 
                  : authMode === 'signup' 
                    ? 'Create Account' 
                    : authMode === 'forgot_password'
                      ? 'Send Reset Link'
                      : 'Reset Password'}
            </button>
          </form>
          
          <div className="mt-4 pt-4 border-t border-slate-100 text-center">
            {authMode === 'forgot_password' || authMode === 'reset_password' ? (
              <button
                onClick={() => {
                  setAuthMode('signin');
                  setAuthError('');
                  setAuthSuccessMessage('');
                  setAuthEmail('');
                  setAuthPassword('');
                  setAuthConfirmPassword('');
                }}
                className="text-xs text-blue-500 hover:text-blue-600 font-medium bg-transparent border-0 cursor-pointer focus:outline-none"
              >
                Back to Sign In
              </button>
            ) : (
              <button
                onClick={() => {
                  setAuthMode(authMode === 'signin' ? 'signup' : 'signin');
                  setAuthError('');
                  setAuthSuccessMessage('');
                  setAuthPassword('');
                }}
                className="text-xs text-blue-500 hover:text-blue-600 font-medium bg-transparent border-0 cursor-pointer focus:outline-none"
              >
                {authMode === 'signin' 
                  ? "Don't have an account? Sign Up" 
                  : "Already have an account? Sign In"}
              </button>
            )}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen w-screen bg-slate-50 font-sans overflow-hidden">
      
      {/* SIDEBAR: PDF Upload & List (Hidden in Focus Mode) */}
      <aside 
        className={`bg-white border-r border-slate-200 flex flex-col h-full shrink-0 transition-all duration-300 overflow-hidden
          ${isFocusMode ? 'w-0 border-r-0' : 'w-[320px]'}
        `}
      >
        {/* Sidebar Header */}
        <div className="p-4 border-b border-slate-200 flex items-center justify-between bg-slate-50">
          <div className="flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-blue-500 stroke-[1.5]" />
            <h1 className="text-sm font-semibold text-slate-800 tracking-tight">
              PDF-to-Mindmap
            </h1>
          </div>
        </div>

        {/* Model Selector & Upload Zone Container */}
        <div className="p-4 border-b border-slate-200 bg-white">
          <div className="mb-4">
            <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block mb-1.5 flex items-center gap-1">
              <Cpu className="w-3 h-3 text-slate-400" />
              Language Model
            </label>
            <select
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
              className="w-full text-xs bg-slate-50 border border-slate-200 text-slate-700 px-2.5 py-1.5 focus:outline-none focus:border-slate-300 font-medium select-none cursor-pointer rounded-none"
            >
              <option value="auto-smart-routing">Auto (Smart Routing) (Recommended)</option>
              <optgroup label="Flagship & Large Models">
                <option value="llama-3.3-70b-versatile">Llama 3.3 70B Versatile (Best Quality)</option>
                <option value="openai/gpt-oss-120b">GPT-OSS 120B (High Capacity)</option>
              </optgroup>
              <optgroup label="Fast & High-Throughput Models">
                <option value="llama-3.1-8b-instant">Llama 3.1 8B Instant (Ultra-Fast 30k TPM)</option>
                <option value="openai/gpt-oss-20b">GPT-OSS 20B (Balanced)</option>
              </optgroup>
            </select>

          </div>

          <div className="mb-4">
            <label className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block mb-1.5 flex items-center gap-1">
              <BookOpen className="w-3 h-3 text-slate-400" />
              Subject Mode
            </label>
            <select
              value={selectedSubject}
              onChange={(e) => setSelectedSubject(e.target.value)}
              className="w-full text-xs bg-slate-50 border border-slate-200 text-slate-700 px-2.5 py-1.5 focus:outline-none focus:border-slate-300 font-medium select-none cursor-pointer rounded-none"
            >
              <option value="general">General (Auto-detect)</option>
              <option value="math">Mathematics (Formulas, Proofs, 2D Plots)</option>
              <option value="physics">Physics (Physical Laws, Variables, Curves)</option>
              <option value="geography">Geography (Processes, Cycles, Diagrams)</option>
              <option value="history">History (Timelines, Causality, Rationale)</option>
            </select>
          </div>

          <UploadZone 
            onMindmapGenerated={handleMindmapGenerated} 
            selectedModel={selectedModel}
            selectedSubject={selectedSubject}
          />
        </div>

        {/* Processed Documents List */}
        <div className="flex-1 overflow-y-auto p-4">
          <h2 className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-2">
            My Documents ({userDocuments.length})
          </h2>
          {userDocuments.length === 0 ? (
            <p className="text-xs text-slate-400 italic">No documents uploaded yet.</p>
          ) : (
            <div className="flex flex-col gap-1.5">
              {userDocuments.map((doc) => (
                <div
                  key={doc.id}
                  role="button"
                  tabIndex={0}
                  onClick={() => handleSelectDocument(doc.id)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') handleSelectDocument(doc.id); }}
                  className={`w-full text-left px-3 py-2.5 border text-xs flex items-center justify-between group transition-none rounded-none cursor-pointer select-none
                    ${doc.id === activeDocId 
                      ? 'bg-slate-50 border-slate-300 text-slate-800 font-semibold' 
                      : 'bg-white border-slate-200 text-slate-600 hover:border-slate-300'}
                  `}
                >
                  <span className="truncate pr-4 leading-normal">{doc.name}</span>
                  <button
                    onClick={(e) => handleDeleteDocument(doc.id, e)}
                    className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-rose-500 p-0.5 transition-none focus:opacity-100 cursor-pointer"
                    title="Delete document"
                  >
                    <Trash2 className="w-3.5 h-3.5 stroke-[1.5]" />
                  </button>
                </div>
              ))}

            </div>
          )}
        </div>
      </aside>

      {/* MAIN CONTAINER: Topbar & Canvas */}
      <main className="flex-1 flex flex-col h-full relative overflow-hidden">
        
        {/* TOPBAR */}
        <header className="h-[52px] bg-white border-b border-slate-200 flex items-center justify-between px-4 shrink-0 select-none">
          <div className="flex items-center gap-3">
            <button
              onClick={() => setIsFocusMode(!isFocusMode)}
              className="p-1.5 border border-slate-200 text-slate-600 hover:bg-slate-50 transition-none rounded-none focus:outline-none flex items-center gap-1.5 text-xs cursor-pointer font-medium"
              title={isFocusMode ? "Exit Focus Mode" : "Enter Focus Mode"}
            >
              {isFocusMode ? (
                <>
                  <Minimize2 className="w-3.5 h-3.5 stroke-[1.5]" />
                  <span>Exit Focus</span>
                </>
              ) : (
                <>
                  <Maximize2 className="w-3.5 h-3.5 stroke-[1.5]" />
                  <span>Focus Mode</span>
                </>
              )}
            </button>

            <button
              onClick={handleUndo}
              disabled={undoHistory.length === 0}
              className={`p-1.5 border text-xs transition-none rounded-none flex items-center gap-1.5 font-medium
                ${undoHistory.length === 0 
                  ? 'border-slate-100 text-slate-300 cursor-not-allowed' 
                  : 'border-slate-200 text-slate-600 hover:bg-slate-50 cursor-pointer'}`}
              title="Undo last node action"
            >
              <Undo2 className="w-3.5 h-3.5 stroke-[1.5]" />
              <span>Undo</span>
            </button>

            <button
              onClick={handleResetAll}
              className="p-1.5 border border-slate-200 text-slate-600 hover:bg-slate-50 transition-none rounded-none focus:outline-none flex items-center gap-1.5 text-xs cursor-pointer font-medium hover:text-rose-600 hover:border-rose-200"
              title="Reset the application state and clear localStorage"
            >
              <RotateCcw className="w-3.5 h-3.5 stroke-[1.5]" />
              <span>Reset Workspace</span>
            </button>
          </div>

          <div className="flex items-center gap-4">
            {activeDoc && (
              <span className="text-xs text-slate-500 font-medium max-w-[200px] truncate" title={activeDoc.name}>
                Active: {activeDoc.name}
              </span>
            )}
            <div className="h-4 w-[1px] bg-slate-200"></div>
            <span className="text-xs text-slate-500 font-medium select-all" title="Logged in user">
              {currentUserEmail}
            </span>
            <button
              onClick={handleSignOut}
              className="p-1.5 border border-slate-200 text-slate-600 hover:bg-slate-50 hover:text-rose-600 hover:border-rose-200 transition-none rounded-none focus:outline-none flex items-center gap-1.5 text-xs cursor-pointer font-medium"
              title="Sign out of your session"
            >
              Sign Out
            </button>
          </div>
        </header>

        {/* CANVAS WORKSPACE */}
        <div className="flex-1 relative overflow-hidden">
          <MindmapCanvas
            mindmap={activeDoc ? activeDoc.data : null}
            expandedIds={expandedIds}
            selectedNodeId={selectedNode ? selectedNode.id : null}
            onToggleNodeExpand={handleToggleNodeExpand}
            onSelectNode={handleSelectNode}
          />
        </div>

        {/* SIDE PANEL: Slide-out detailed summary */}
        <div 
          className={`absolute top-0 right-0 h-full bg-white border-l border-slate-200 shadow-xl transition-all duration-300 z-[1000] flex flex-col overflow-hidden
            ${selectedNode ? 'w-[380px]' : 'w-0 border-l-0'}
          `}
        >
          {selectedNode && (
            <>
              {/* Header */}
              <div className="p-4 border-b border-slate-100 flex items-center justify-between bg-slate-50 select-none">
                <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">
                  Details Panel
                </span>
                <button
                  onClick={() => setSelectedNode(null)}
                  className="text-slate-400 hover:text-slate-600 p-0.5 hover:bg-slate-100 transition-none"
                >
                  <X className="w-4 h-4 stroke-[1.5]" />
                </button>
              </div>

              {/* Content */}
              <div className="flex-1 overflow-y-auto p-5 space-y-4">
                <div>
                  <h3 className="text-base font-bold text-slate-800 leading-snug mb-1.5 font-sans">
                    {selectedNode.label}
                  </h3>
                  <div className="h-[2px] bg-slate-100 w-10"></div>
                </div>

                {/* 1. Educational Image (if attached) */}
                {selectedNode.imageUrl && (
                  <div className="w-full bg-slate-100 rounded-md border border-slate-100 overflow-hidden">
                    <img 
                      src={selectedNode.imageUrl} 
                      alt={selectedNode.imageCaption || selectedNode.label}
                      className="w-full max-h-[180px] object-cover"
                      loading="lazy"
                    />
                    {selectedNode.imageCaption && (
                      <div className="p-2 bg-slate-50 border-t border-slate-100">
                        <span className="text-[10px] text-slate-500 font-sans block italic">
                          {selectedNode.imageCaption}
                        </span>
                      </div>
                    )}
                  </div>
                )}

                {/* 2. Structured KaTeX & Markdown Summary */}
                <div className="space-y-2 pt-1">
                  <MathRenderer content={selectedNode.summary} />
                </div>
              </div>
            </>
          )}
        </div>


      </main>
    </div>
  );
}

