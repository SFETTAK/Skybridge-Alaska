"use strict";

// SkyBridge Alaska — Combined ADS-B View
// Local receiver + ADSB.fi statewide feed

PageName = "SkyBridge AK";

// Center on Anchorage
DefaultCenterLat = 61.17;
DefaultCenterLon = -150.0;
DefaultZoomLvl = 6;

// Show site marker at DOT-VHF ground station
SiteShow = true;
SiteLat = 61.1744;
SiteLon = -149.9964;
SiteName = "DOT-VHF Ground Station";

// Nautical units for aviation
DisplayUnits = "nautical";

// ESRI satellite base map
MapType_tar1090 = "esri";

// Dark mode
darkModeDefault = true;

// Show flags and registration links
ShowFlags = true;
registrationLinks = true;

// Enable VFR sectional overlay by default
defaultOverlays = ['nexrad'];

// 8 hours of tracks
PTRACKS = 8;

// Show aircraft count in title
PlaneCountInTitle = true;

// Enable aircraft photos
showPictures = true;
planespottersAPI = true;

// Route API
useRouteAPI = true;

// Range rings at 50, 100, 200, 300nm
SiteCircles = true;
SiteCirclesDistances = new Array(50, 100, 200, 300);
SiteCirclesColors = ['#00d4aa44', '#0090ff44', '#ffaa0044', '#ff444444'];

// Labels
labelZoom = 7;

// Filter stale positions
seenTimeout = 120;

// GPS / Geolocation — follow pilot position on map
updateLocation = true;   // keep map centered on GPS
askLocation = true;       // prompt for location permission (requires HTTPS)
disableGeoLocation = false;

// Auto-select nearest plane to GPS position
// autoselectCoords set dynamically by GPS
