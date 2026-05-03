import { useState } from 'react';
import { supabase } from './supabaseClient';
import { useNavigate } from 'react-router-dom';

export default function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState(null);
  const navigate = useNavigate();

  const handleLogin = async (e) => {
    e.preventDefault();
    const { data, error } = await supabase.auth.signInWithPassword({ email, password });
    
    if (error) {
      setError(error.message);
    } else {
      // Save the token to localStorage so our API can use it
      localStorage.setItem('access_token', data.session.access_token);
      navigate('/dashboard');
    }
  };

  return (
    <div style={{ padding: '50px', maxWidth: '400px', margin: '0 auto' }}>
      <h2>SaaS Login</h2>
      {error && <p style={{ color: 'red' }}>{error}</p>}
      <form onSubmit={handleLogin} style={{ display: 'flex', flexDirection: 'column', gap: '15px' }}>
        <input 
          type="email" placeholder="Email" value={email} 
          onChange={(e) => setEmail(e.target.value)} required 
        />
        <input 
          type="password" placeholder="Password" value={password} 
          onChange={(e) => setPassword(e.target.value)} required 
        />
        <button type="submit">Login</button>
      </form>
    </div>
  );
}