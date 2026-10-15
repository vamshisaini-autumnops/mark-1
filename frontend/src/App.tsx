import { useEffect, useState } from 'react'
import { supabase } from './lib/supabase'

function App() {
  const [message, setMessage] = useState('Checking connection…')
  const [invoiceFile, setInvoiceFile] = useState<File | null>(null)
  const [uploadMessage, setUploadMessage] = useState('')
  const [isUploading, setIsUploading] = useState(false)

  useEffect(() => {
    async function checkConnection() {
      try {
        const { data, error } = await supabase
          .from('connection_test')
          .select('message')
          .eq('id', 1)
          .single()

        if (error) throw error

        setMessage(data.message)
      } catch (error) {
        setMessage(
          `Connection failed: ${
            error instanceof Error
              ? error.message
              : String(error)
          }`,
        )
      }
    }

    void checkConnection()
  }, [])

  async function handleUpload() {
    if (!invoiceFile || isUploading) return

    const formData = new FormData()
    formData.append('invoice', invoiceFile)

    setIsUploading(true)
    setUploadMessage('Uploading and processing invoice…')
    try {
      const response = await fetch('/api/invoices', {
        method: 'POST',
        body: formData,
      })
      const data = await response.json()
      if (!response.ok) {
        const detail = data.detail
        throw new Error(
          typeof detail === 'object' && detail?.message
            ? `${detail.message} Folder: ${detail.invoice_id}`
            : typeof detail === 'string' ? detail : `HTTP ${response.status}`,
        )
      }
      setUploadMessage(`Processed ${data.filename}. Original, JSON, and CSV saved in invoices/${data.invoice_id}/`)
    } catch (error) {
      setUploadMessage(`Upload failed: ${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setIsUploading(false)
    }
  }

  return (
    <main>
      <h1>Autumn Ops — Mark 1</h1>
      <p>{message}</p>
      <label>
        Select invoice
        <input
          type="file"
          accept="image/*,application/pdf"
          disabled={isUploading}
          onChange={(event) => setInvoiceFile(event.target.files?.[0] ?? null)}
        />
      </label>
      {invoiceFile && <p>Selected: {invoiceFile.name}</p>}
      <button type="button" disabled={!invoiceFile || isUploading} onClick={handleUpload}>
        {isUploading ? 'Processing…' : 'Upload invoice'}
      </button>
      <p role="status">{uploadMessage}</p>
    </main>
  )
}

export default App
