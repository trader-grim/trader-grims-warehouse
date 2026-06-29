"""Tests for eBay sync.fetch_all_offers() error handling."""
from unittest.mock import Mock, patch

import pytest
import requests

from tgw.ebay.sync import fetch_all_offers

def test_404_returns_empty():
    """404 response → return empty list."""
    mock_resp = requests.Response()
    mock_resp.status_code = 404
    
    with patch('tgw.ebay.sync.ebay_get', side_effect=requests.exceptions.HTTPError(response=mock_resp)):
        assert fetch_all_offers({}) == []

@pytest.mark.parametrize('error_id', [25702, 25710, 25009])
def test_400_known_errors_graceful_empty(error_id):
    """400 with known errorIds → return empty list."""
    mock_resp = requests.Response()
    mock_resp.status_code = 400
    mock_resp.json = lambda: {'errors': [{'errorId': str(error_id)}]}
    
    with patch('tgw.ebay.sync.ebay_get', side_effect=requests.exceptions.HTTPError(response=mock_resp)):
        assert fetch_all_offers({}) == []

def test_400_unknown_error_raises():
    """400 with unknown errorId → re-raises."""
    mock_resp = requests.Response()
    mock_resp.status_code = 400
    mock_resp.json = lambda: {'errors': [{'errorId': '25707'}]}
    
    with patch('tgw.ebay.sync.ebay_get', side_effect=requests.exceptions.HTTPError(response=mock_resp)):
        with pytest.raises(requests.exceptions.HTTPError):
            fetch_all_offers({})

def test_400_unparseable_json_raises():
    """400 with invalid JSON → re-raises."""
    mock_resp = requests.Response()
    mock_resp.status_code = 400
    mock_resp.json = Mock(side_effect=ValueError("bad json"))
    
    with patch('tgw.ebay.sync.ebay_get', 
              side_effect=requests.exceptions.HTTPError(response=mock_resp)):
        with pytest.raises(requests.exceptions.HTTPError):
            fetch_all_offers({})

def test_happy_path_returns_offers():
    """200 response with offers → return them."""
    mock_resp = {'offers': [1,2,3], 'total': 3}
    
    with patch('tgw.ebay.sync.ebay_get', return_value=mock_resp):
        assert fetch_all_offers({}) == [1,2,3]

def test_pagination_collects_all():
    """Paginated responses → collect all items."""
    page1 = {'offers': [1,2], 'total': 150}  # total > limit to trigger pagination
    page2 = {'offers': [3], 'total': 150}
    
    with patch('tgw.ebay.sync.ebay_get', side_effect=[page1, page2]):
        assert fetch_all_offers({}) == [1,2,3]
