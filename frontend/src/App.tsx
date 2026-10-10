import { useEffect, useState } from 'react'
import { supabase } from './lib/supabase'

function App() {
  const [message, setMessage] = useState('Checking connection…')

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

  return (
    <main>
      <h1>Autumn Ops — Mark 1</h1>
      <p>{message}</p>
    </main>
  )
}

export default App