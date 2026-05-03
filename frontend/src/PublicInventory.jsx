import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000';

export default function PublicInventory() {
  const { token } = useParams();
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    const fetchPublicInventory = async () => {
      try {
        const response = await axios.get(`${API_BASE_URL}/public/inventory/${token}`);
        setData(response.data);
      } catch (err) {
        setError(err?.response?.data?.detail || 'Unable to load shared inventory link.');
      }
    };
    fetchPublicInventory();
  }, [token]);

  if (error) {
    return (
      <div style={{ padding: '32px', fontFamily: 'sans-serif' }}>
        <h2>Shared Inventory</h2>
        <p style={{ color: 'crimson' }}>{error}</p>
      </div>
    );
  }

  if (!data) {
    return <div style={{ padding: '32px', fontFamily: 'sans-serif' }}>Loading shared inventory...</div>;
  }

  const summary = data.summary || {};
  return (
    <div style={{ padding: '32px', fontFamily: 'sans-serif' }}>
      <h2>Shared Inventory Snapshot</h2>
      <p>Scope: {data.scope}</p>
      <p>Expires at: {data.expires_at}</p>
      <p>Total unique items: {summary.total_unique_items}</p>
      <p>Total stock units: {summary.total_stock_units}</p>
      <p>Total retail value: {summary.total_retail_value}</p>
      <p>Low stock alerts: {summary.low_stock_alerts}</p>

      <h3>Low Stock Items</h3>
      {(summary.low_stock_items || []).length === 0 ? (
        <p>No low stock items.</p>
      ) : (
        <ul>
          {summary.low_stock_items.map((item) => (
            <li key={`${item.sku_code || item.product_name}`}>
              {item.product_name} ({item.sku_code || 'NA'}) - {item.stock_quantity}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
