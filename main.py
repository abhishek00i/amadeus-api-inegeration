from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles  # Import StaticFiles
import requests
import os
from datetime import datetime, timedelta
import re
from starlette.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()
# time , date , flight details
app = FastAPI(title="Flight Price API",
              description="An API to get the cheapest flight prices for one-way and round-trip journeys.")

# Since we're serving the frontend from the same server, we can remove CORS middleware
# and simplify the setup. However, keeping it is harmless for a development environment.
origins = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Amadeus API credentials from environment variables
AMADEUS_API_KEY = os.environ.get("AMADEUS_CLIENT_ID")
AMADEUS_API_SECRET = os.environ.get("AMADEUS_CLIENT_SECRET")

# Amadeus API token
token = ""
token_expiry = None

def get_amadeus_token():
    """Fetches a new access token from the Amadeus API."""
    global token, token_expiry
    url = "https://test.api.amadeus.com/v1/security/oauth2/token"
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {
        "grant_type": "client_credentials",
        "client_id": AMADEUS_API_KEY,
        "client_secret": AMADEUS_API_SECRET,
    }
    try:
        response = requests.post(url, headers=headers, data=data)
        response.raise_for_status()
        token_data = response.json()
        token = token_data["access_token"]
        token_expiry = datetime.now() + timedelta(seconds=token_data["expires_in"])
        print("Successfully obtained a new Amadeus access token.")
    except requests.exceptions.RequestException as e:
        print(f"Error getting Amadeus token: {e}")
        raise HTTPException(status_code=500, detail="Failed to authenticate with Amadeus API.")

def is_token_valid():
    """Checks if the current access token is still valid."""
    global token, token_expiry
    return token and token_expiry and token_expiry > datetime.now() + timedelta(minutes=5)


@app.get("/flight-price", summary="Get flight price for a one-way or two-way journey")
async def get_flight_price(
    origin: str,
    destination: str,
    departure_date: str,
    duration: str = None
):
    """
    Get the cheapest flight price with detailed itinerary.
    """
    if not is_token_valid():
        get_amadeus_token()

    url = "https://test.api.amadeus.com/v2/shopping/flight-offers"
    
    params = {
        "originLocationCode": origin,
        "destinationLocationCode": destination,
        "departureDate": departure_date,
        "adults": 1,
        "currencyCode": "INR",
        "max": 10
    }

    if duration:
        try:
            match = re.match(r"(\d+)\s*days", duration, re.IGNORECASE)
            if not match:
                raise ValueError("Invalid duration format. Please use 'X days'.")
            
            days_to_add = int(match.group(1))
            dep_date_obj = datetime.strptime(departure_date, "%Y-%m-%d").date()
            return_date_obj = dep_date_obj + timedelta(days=days_to_add)
            params["returnDate"] = return_date_obj.strftime("%Y-%m-%d")

        except (ValueError, IndexError) as e:
            raise HTTPException(status_code=400, detail=str(e))
    
    headers = {
        "Authorization": f"Bearer {token}"
    }

    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        
        flight_offers = response.json().get("data", [])
        
        if not flight_offers:
            return {"message": "No flight offers found for the specified criteria."}
        
        # Find the cheapest overall offer
        cheapest_offer = min(flight_offers, key=lambda x: float(x["price"]["grandTotal"]))

        # --- NEW CODE TO PARSE THE DETAILS ---

        # Helper function to parse segments of an itinerary
        def parse_itinerary_details(itinerary):
            segments_info = []
            for segment in itinerary['segments']:
                segments_info.append({
                    "airline_code": segment['carrierCode'],
                    "flight_number": segment['number'],
                    "departure_airport": segment['departure']['iataCode'],
                    "departure_time": segment['departure']['at'],
                    "arrival_airport": segment['arrival']['iataCode'],
                    "arrival_time": segment['arrival']['at'],
                    "duration": segment['duration']
                })
            return {
                "total_duration": itinerary['duration'],
                "segments": segments_info
            }

        # The first itinerary is always the outbound journey
        outbound_journey = parse_itinerary_details(cheapest_offer['itineraries'][0])
        
        response_data = {
            "journey_type": "two-way" if duration else "one-way",
            "total_price_in_inr": float(cheapest_offer["price"]["grandTotal"]),
            "outbound_journey": outbound_journey
        }
        
        # If it's a two-way trip, parse the second itinerary for the return journey
        if duration and len(cheapest_offer['itineraries']) > 1:
            return_journey = parse_itinerary_details(cheapest_offer['itineraries'][1])
            response_data["return_journey"] = return_journey
            # Calculate return date for clarity in the response
            response_data["return_date"] = params.get("returnDate")

        print("Start :  --  ",response_data, "  --  End")
        return response_data
        
    except requests.exceptions.RequestException as e:
        detail = f"An error occurred while fetching flight data: {e}"
        # A more robust way to handle potential response errors
        try:
            error_json = response.json()
            if response.status_code == 400:
                detail = f"Bad request to Amadeus API. Please check your parameters. Error: {error_json}"
            elif response.status_code == 401:
                detail = "Amadeus authentication failed. Please check your API key and secret."
            elif response.status_code == 404:
                detail = "The requested resource was not found on the Amadeus API."
            raise HTTPException(status_code=response.status_code, detail=detail)
        except (ValueError, AttributeError): # If response is not JSON or doesn't have status_code
             raise HTTPException(status_code=500, detail=str(e))

# Mount the static files (like index.html) to the root of the server
app.mount("/", StaticFiles(directory=".", html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    get_amadeus_token() 
    uvicorn.run(app, host="0.0.0.0", port=8000)
