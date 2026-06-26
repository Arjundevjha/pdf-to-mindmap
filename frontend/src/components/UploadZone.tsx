import React, { useState, useRef } from 'react';
import { Upload, AlertCircle, FileText, CheckCircle } from 'lucide-react';

interface UploadZoneProps {
  onMindmapGenerated: (filename: string, data: any) => void;
  selectedModel: string;
}

export function UploadZone({ onMindmapGenerated, selectedModel }: UploadZoneProps) {
  const [dragActive, setDragActive] = useState(false);
  const [status, setStatus] = useState<'idle' | 'extracting' | 'generating' | 'success' | 'error'>('idle');
  const [progress, setProgress] = useState(0);
  const [errorMessage, setErrorMessage] = useState('');
  const [fileName, setFileName] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  const API_BASE = import.meta.env.VITE_API_BASE_URL 
    ? `${import.meta.env.VITE_API_BASE_URL}/api` 
    : 'http://localhost:8000/api'; // FastAPI server URL

  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      const file = e.dataTransfer.files[0];
      if (file.type === "application/pdf" || file.name.endsWith('.pdf')) {
        processFile(file);
      } else {
        showError("Invalid file type. Please upload a PDF.");
      }
    }
  };

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    e.preventDefault();
    if (e.target.files && e.target.files[0]) {
      processFile(e.target.files[0]);
    }
  };

  const onButtonClick = () => {
    inputRef.current?.click();
  };

  const showError = (message: string) => {
    setStatus('error');
    setErrorMessage(message);
    setProgress(0);
  };

  const processFile = async (file: File) => {
    try {
      setStatus('extracting');
      setProgress(15);
      setFileName(file.name);
      setErrorMessage('');

      // Create FormData
      const formData = new FormData();
      formData.append('file', file);

      // Step 1: Upload and extract text
      const uploadResponse = await fetch(`${API_BASE}/upload-pdf`, {
        method: 'POST',
        body: formData,
      });

      if (!uploadResponse.ok) {
        const errorData = await uploadResponse.json();
        throw new Error(errorData.detail || "Failed to extract text from PDF.");
      }

      setProgress(40);
      const uploadResult = await uploadResponse.json();
      const extractedText = uploadResult.text;

      // Step 2: Generate Mindmap
      setStatus('generating');
      setProgress(60);

      const generateResponse = await fetch(`${API_BASE}/generate-mindmap`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          text: extractedText,
          model: selectedModel,
        }),
      });

      if (!generateResponse.ok) {
        const errorData = await generateResponse.json();
        throw new Error(errorData.detail || "Failed to generate mindmap from text.");
      }

      setProgress(90);
      const mindmapData = await generateResponse.json();

      // Step 3: Complete
      setStatus('success');
      setProgress(100);
      setTimeout(() => {
        onMindmapGenerated(file.name, mindmapData);
        // Reset state after a brief delay
        setStatus('idle');
        setProgress(0);
        setFileName('');
      }, 800);

    } catch (err: any) {
      showError(err.message || "An unexpected error occurred during processing.");
    }
  };

  return (
    <div className="w-full">
      <div 
        onDragEnter={handleDrag} 
        onDragOver={handleDrag} 
        onDragLeave={handleDrag} 
        onDrop={handleDrop}
        onClick={status === 'idle' || status === 'error' ? onButtonClick : undefined}
        className={`w-full border border-dashed p-6 flex flex-col items-center justify-center bg-white cursor-pointer select-none border-slate-200 transition-colors
          ${dragActive ? 'border-blue-400 bg-slate-50' : 'hover:border-slate-300'}
          ${status !== 'idle' && status !== 'error' ? 'cursor-not-allowed opacity-80' : ''}
        `}
      >
        <input 
          ref={inputRef} 
          type="file" 
          className="hidden" 
          accept=".pdf" 
          onChange={handleChange}
          disabled={status !== 'idle' && status !== 'error'}
        />
        
        <Upload className="w-8 h-8 text-slate-400 mb-2 stroke-[1.5]" />
        
        <p className="text-slate-700 text-sm font-medium mb-1">
          Click to upload or drag & drop
        </p>
        <p className="text-slate-400 text-xs">
          PDF files only
        </p>
      </div>

      {status !== 'idle' && (
        <div className="mt-4 p-4 border border-slate-200 bg-white">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
              {status === 'extracting' && "Step 1: Extracting text..."}
              {status === 'generating' && "Step 2: AI structuring..."}
              {status === 'success' && "Step 3: Rendering canvas..."}
              {status === 'error' && "Processing failed"}
            </span>
            <span className="text-xs font-medium text-slate-600">
              {progress}%
            </span>
          </div>

          {/* Predictable, flat progress bar */}
          <div className="w-full h-1 bg-slate-100 rounded-none overflow-hidden">
            <div 
              className={`h-full transition-all duration-300 ${status === 'error' ? 'bg-rose-400' : 'bg-blue-500'}`}
              style={{ width: `${progress}%` }}
            ></div>
          </div>

          <div className="mt-3 flex items-start gap-2">
            {status === 'error' ? (
              <AlertCircle className="w-4 h-4 text-rose-500 shrink-0 mt-0.5" />
            ) : status === 'success' ? (
              <CheckCircle className="w-4 h-4 text-emerald-500 shrink-0 mt-0.5" />
            ) : (
              <FileText className="w-4 h-4 text-slate-400 shrink-0 mt-0.5 animate-pulse" />
            )}
            <div className="flex-1 min-w-0">
              {status === 'error' ? (
                <p className="text-xs text-rose-600 leading-normal font-medium break-words">
                  {errorMessage}
                </p>
              ) : (
                <p className="text-xs text-slate-600 truncate" title={fileName}>
                  {fileName}
                </p>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
