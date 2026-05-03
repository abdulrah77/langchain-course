import { useEffect, useState } from 'react';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';
import InventoryList from './InventoryList';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';

export default function Dashboard() {
  const [stats, setStats] = useState(null);
  const [error, setError] = useState(null);
  const navigate = useNavigate();

  useEffect(() => {
    const fetchDashboard = async () => {
      const token = localStorage.getItem('access_token');
      if (!token) {
        navigate('/'); // Send back to login if no token
        return;
      }

      try {
        const response = await axios.get(`${API_BASE_URL}/api/inventory/stats/dashboard`, {
          headers: {
            Authorization: `Bearer ${token}`
          }
        });
        setStats(response.data);
      } catch (err) {
        setError('Failed to fetch stats. Token might be expired.');
        localStorage.removeItem('access_token');
      }
    };

    fetchDashboard();
  }, [navigate]);

  return (
    <div style={{ padding: '50px', fontFamily: 'sans-serif' }}>
      <h1>Inventory Dashboard</h1>
      <button onClick={() => { localStorage.clear(); navigate('/'); }}>Logout</button>
      
      {error && <p style={{ color: 'red' }}>{error}</p>}
      
      {stats ? (
        <div style={{ display: 'flex', gap: '20px', marginTop: '30px' }}>
          <div style={{ border: '1px solid #ccc', padding: '20px', borderRadius: '8px' }}>
            <h3>Total Items</h3>
            <p style={{ fontSize: '24px', fontWeight: 'bold' }}>{stats.total_unique_items}</p>
          </div>
          <div style={{ border: '1px solid #ccc', padding: '20px', borderRadius: '8px' }}>
            <h3>Retail Value</h3>
            <p style={{ fontSize: '24px', fontWeight: 'bold', color: 'green' }}>
              ${stats.total_retail_value}
            </p>
          </div>
          <div style={{ border: '1px solid #ccc', padding: '20px', borderRadius: '8px' }}>
            <h3>Low Stock Alerts</h3>
            <p style={{ fontSize: '24px', fontWeight: 'bold', color: 'red' }}>
              {stats.low_stock_alerts}
            </p>
          </div>
          
        </div>
        
      ) : (
        <p>Loading stats...</p>
      )}
      <InventoryList />
    </div>
  );
}