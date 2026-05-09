"""
California county → cities/communities lookup.

All 58 counties. Includes every incorporated city plus major unincorporated
communities and census-designated places commonly written on petitions.
"""
from __future__ import annotations

CA_COUNTY_CITIES: dict[str, set[str]] = {
    "Alameda": {
        "Alameda", "Albany", "Berkeley", "Dublin", "Emeryville", "Fremont",
        "Hayward", "Livermore", "Newark", "Oakland", "Piedmont", "Pleasanton",
        "San Leandro", "Union City", "Castro Valley", "San Lorenzo", "Cherryland",
        "Ashland", "Fairview", "Sunol", "Unincorporated Alameda",
    },
    "Alpine": {"Markleeville", "Woodfords", "Bear Valley", "Kirkwood", "Hung-a-lel-ti"},
    "Amador": {
        "Amador City", "Ione", "Jackson", "Plymouth", "Sutter Creek",
        "Pine Grove", "Pioneer", "Martell", "Drytown", "Fiddletown",
    },
    "Butte": {
        "Biggs", "Chico", "Gridley", "Oroville", "Paradise",
        "Durham", "Forest Ranch", "Magalia", "Thermalito", "Palermo",
        "Berry Creek", "Clipper Mills", "Feather Falls", "Forbestown",
    },
    "Calaveras": {
        "Angels Camp", "Arnold", "Copperopolis", "Murphys", "San Andreas",
        "Valley Springs", "West Point", "Mokelumne Hill", "Glencoe",
    },
    "Colusa": {"Colusa", "Williams", "Arbuckle", "Stonyford", "Maxwell", "Princeton"},
    "Contra Costa": {
        "Antioch", "Brentwood", "Clayton", "Concord", "Danville", "El Cerrito",
        "Hercules", "Lafayette", "Martinez", "Moraga", "Oakley", "Orinda",
        "Pinole", "Pittsburg", "Pleasant Hill", "Richmond", "San Pablo",
        "San Ramon", "Walnut Creek", "Bay Point", "Bethel Island", "Briones",
        "Byron", "Discovery Bay", "El Sobrante", "Kensington", "Knightsen",
        "Rodeo", "Clyde", "Crockett", "Port Costa",
    },
    "Del Norte": {"Crescent City", "Gasquet", "Smith River", "Hiouchi", "Klamath"},
    "El Dorado": {
        "El Dorado Hills", "Placerville", "South Lake Tahoe", "Shingle Springs",
        "Cameron Park", "Diamond Springs", "Georgetown", "Coloma", "Cool",
        "Garden Valley", "Rescue", "El Dorado", "Greenwood", "Pollock Pines",
        "Tahoma", "Twin Bridges",
    },
    "Fresno": {
        "Clovis", "Coalinga", "Fowler", "Fresno", "Huron", "Kerman",
        "Kingsburg", "Mendota", "Orange Cove", "Parlier", "Reedley",
        "Sanger", "San Joaquin", "Selma", "Firebaugh", "Auberry",
        "Calwa", "Caruthers", "Del Rey", "Easton", "Friant", "Laton",
        "Malaga", "Tranquillity", "Biola", "Cantua Creek", "Helm",
        "Lanare", "Tarpey Village",
    },
    "Glenn": {"Orland", "Willows", "Hamilton City", "Elk Creek", "Artois", "Bayliss"},
    "Humboldt": {
        "Arcata", "Blue Lake", "Eureka", "Ferndale", "Fortuna", "Rio Dell",
        "Trinidad", "McKinleyville", "Bayside", "Cutten", "Myrtletown",
        "Pine Hill", "Redway", "Samoa", "Shelter Cove", "Garberville",
        "Willow Creek", "Hoopa", "Scotia",
    },
    "Imperial": {
        "Brawley", "Calexico", "Calipatria", "El Centro", "Holtville",
        "Imperial", "Westmorland", "Heber", "Seeley", "El Rio",
    },
    "Inyo": {
        "Bishop", "Big Pine", "Independence", "Lone Pine", "Mammoth Lakes",
        "Death Valley", "Olancha", "Tecopa",
    },
    "Kern": {
        "Arvin", "Bakersfield", "California City", "Delano", "Maricopa",
        "McFarland", "Ridgecrest", "Shafter", "Taft", "Tehachapi",
        "Wasco", "Buttonwillow", "Edison", "Frazier Park", "Lamont",
        "Lake Isabella", "Lebec", "Mojave", "Oildale", "Rosamond",
        "Stallion Springs", "Wofford Heights", "Boron", "Edwards",
        "Tupman", "Wheeler Ridge",
    },
    "Kings": {
        "Avenal", "Corcoran", "Hanford", "Lemoore", "Armona", "Laton",
        "Stratford", "Hardwick",
    },
    "Lake": {
        "Clearlake", "Clearlake Oaks", "Lakeport", "Lucerne",
        "Middletown", "Nice", "Upper Lake", "Kelseyville", "Lower Lake",
    },
    "Lassen": {"Susanville", "Bieber", "Herlong", "Standish", "Janesville"},
    "Los Angeles": {
        "Agoura Hills", "Alhambra", "Arcadia", "Artesia", "Avalon",
        "Azusa", "Baldwin Park", "Bell", "Bellflower", "Bell Gardens",
        "Beverly Hills", "Bradbury", "Burbank", "Calabasas", "Carson",
        "Cerritos", "Claremont", "Commerce", "Compton", "Covina",
        "Cudahy", "Culver City", "Diamond Bar", "Downey", "Duarte",
        "El Monte", "El Segundo", "Gardena", "Glendale", "Glendora",
        "Hawaiian Gardens", "Hawthorne", "Hermosa Beach", "Hidden Hills",
        "Huntington Park", "Industry", "Inglewood", "Irwindale",
        "La Canada Flintridge", "La Habra Heights", "La Mirada",
        "La Puente", "La Verne", "Lakewood", "Lancaster", "Lawndale",
        "Lomita", "Long Beach", "Los Angeles", "Lynwood", "Malibu",
        "Manhattan Beach", "Maywood", "Monrovia", "Montebello",
        "Monterey Park", "Norwalk", "Palmdale", "Palos Verdes Estates",
        "Paramount", "Pasadena", "Pico Rivera", "Pomona",
        "Rancho Palos Verdes", "Redondo Beach", "Rolling Hills",
        "Rolling Hills Estates", "Rosemead", "San Dimas", "San Fernando",
        "San Gabriel", "San Marino", "Santa Clarita", "Santa Fe Springs",
        "Santa Monica", "Sierra Madre", "Signal Hill", "South El Monte",
        "South Gate", "South Pasadena", "Temple City", "Torrance",
        "Vernon", "Walnut", "West Covina", "West Hollywood",
        "Westlake Village", "Whittier",
        # Major unincorporated communities
        "Acton", "Agua Dulce", "Altadena", "Avocado Heights", "Azusa",
        "Castaic", "Chatsworth", "East Los Angeles", "El Sereno",
        "Hacienda Heights", "La Crescenta", "Lake Los Angeles",
        "Lennox", "Littlerock", "Llano", "Los Nietos", "Newhall",
        "North Hollywood", "Northridge", "Reseda", "Rowland Heights",
        "San Pedro", "Stevenson Ranch", "Studio City", "Sun Valley",
        "Sunland", "Sylmar", "Topanga", "Tujunga", "Universal City",
        "Valencia", "Van Nuys", "View Park", "West Athens", "West Carson",
        "West Hills", "Westchester", "Willowbrook", "Winnetka",
        "Woodland Hills", "Canyon Country", "Canoga Park",
    },
    "Madera": {
        "Chowchilla", "Madera", "Bonadelle Ranchos", "Fairmead",
        "North Fork", "Oakhurst", "Raymond", "Madera Acres",
    },
    "Marin": {
        "Belvedere", "Corte Madera", "Fairfax", "Larkspur", "Mill Valley",
        "Novato", "Ross", "San Anselmo", "San Rafael", "Sausalito",
        "Tiburon", "Bolinas", "Dillon Beach", "Forest Knolls",
        "Inverness", "Kentfield", "Muir Beach", "Nicasio", "Olema",
        "Point Reyes Station", "San Geronimo", "Stinson Beach",
        "Tomales", "Woodacre", "Lagunitas",
    },
    "Mariposa": {
        "Mariposa", "El Portal", "Catheys Valley", "Coulterville", "Yosemite Valley",
    },
    "Mendocino": {
        "Fort Bragg", "Point Arena", "Ukiah", "Willits",
        "Boonville", "Hopland", "Laytonville", "Redwood Valley",
        "Talmage", "Westport", "Albion", "Gualala", "Leggett",
        "Mendocino", "Philo", "Potter Valley",
    },
    "Merced": {
        "Atwater", "Dos Palos", "Gustine", "Livingston", "Los Banos",
        "Merced", "Planada", "Winton", "Delhi", "Le Grand",
    },
    "Modoc": {"Alturas", "Canby", "Cedarville", "Tulelake"},
    "Mono": {"Mammoth Lakes", "Bridgeport", "June Lake", "Lee Vining", "Benton"},
    "Monterey": {
        "Carmel-by-the-Sea", "Del Rey Oaks", "Gonzales", "Greenfield",
        "King City", "Marina", "Monterey", "Pacific Grove", "Salinas",
        "Sand City", "Seaside", "Soledad", "Prunedale", "Castroville",
        "Carmel Valley", "Carmel", "Monterey County", "Bradley",
        "Moss Landing",
    },
    "Napa": {
        "American Canyon", "Calistoga", "Napa", "St. Helena", "Yountville",
        "Angwin", "Pope Valley", "Rutherford", "Oakville", "Saint Helena",
    },
    "Nevada": {
        "Grass Valley", "Nevada City", "Truckee", "Penn Valley",
        "Alta Sierra", "Rough and Ready", "Chicago Park", "Smartsville",
        "Washington", "Colfax",
    },
    "Orange": {
        "Aliso Viejo", "Anaheim", "Brea", "Buena Park", "Costa Mesa",
        "Cypress", "Dana Point", "Fountain Valley", "Fullerton",
        "Garden Grove", "Huntington Beach", "Irvine", "Laguna Beach",
        "Laguna Hills", "Laguna Niguel", "Laguna Woods", "La Habra",
        "Lake Forest", "La Palma", "Los Alamitos", "Mission Viejo",
        "Newport Beach", "Orange", "Placentia", "Rancho Santa Margarita",
        "San Clemente", "San Juan Capistrano", "Santa Ana", "Seal Beach",
        "Stanton", "Tustin", "Villa Park", "Westminster", "Yorba Linda",
        "Coto de Caza", "Ladera Ranch", "Las Flores", "Midway City",
        "Rossmoor", "Sunset Beach", "Surfside",
    },
    "Placer": {
        "Auburn", "Colfax", "Lincoln", "Loomis", "Rocklin", "Roseville",
        "Citrus Heights", "Granite Bay", "Meadow Vista", "Penryn",
        "Tahoe City", "Kings Beach", "Carnelian Bay", "Foresthill",
        "Sheridan", "Weimar",
    },
    "Plumas": {"Portola", "Quincy", "Chester", "Greenville", "Blairsden"},
    "Riverside": {
        "Banning", "Beaumont", "Blythe", "Calimesa", "Canyon Lake",
        "Cathedral City", "Coachella", "Corona", "Desert Hot Springs",
        "Eastvale", "Hemet", "Indian Wells", "Indio", "Jurupa Valley",
        "Lake Elsinore", "La Quinta", "Menifee", "Moreno Valley",
        "Murrieta", "Norco", "Palm Desert", "Palm Springs",
        "Perris", "Rancho Mirage", "Riverside", "San Jacinto",
        "Temecula", "Wildomar", "Mecca", "Thermal", "Thousand Palms",
        "Desert Center", "Desert Shores", "Idyllwild", "Nuevo",
        "Sun City", "Temescal Valley", "Valle Vista",
    },
    "Sacramento": {
        "Citrus Heights", "Elk Grove", "Folsom", "Galt", "Isleton",
        "Rancho Cordova", "Sacramento", "Arden-Arcade", "Carmichael",
        "Fair Oaks", "Gold River", "La Riviera", "North Highlands",
        "Orangevale", "Rancho Murieta", "Rio Linda", "Rosemont",
        "Antelope", "Elverta", "Herald", "Sloughhouse",
    },
    "San Benito": {"Hollister", "San Juan Bautista", "Aromas", "Tres Pinos"},
    "San Bernardino": {
        "Adelanto", "Apple Valley", "Barstow", "Big Bear Lake",
        "Chino", "Chino Hills", "Colton", "Fontana", "Grand Terrace",
        "Hesperia", "Highland", "Loma Linda", "Montclair", "Needles",
        "Ontario", "Rancho Cucamonga", "Redlands", "Rialto",
        "San Bernardino", "Twentynine Palms", "Upland", "Victorville",
        "Yucaipa", "Yucca Valley", "Bloomington", "Devore", "Joshua Tree",
        "Landers", "Lytle Creek", "Mentone", "Phelan", "Pinon Hills",
        "Running Springs", "Twin Peaks", "Wrightwood", "Big Bear City",
        "Crestline", "Lake Arrowhead", "Lucerne Valley", "Oak Hills",
    },
    "San Diego": {
        "Carlsbad", "Chula Vista", "Coronado", "Del Mar", "El Cajon",
        "Encinitas", "Escondido", "Imperial Beach", "La Mesa",
        "Lemon Grove", "National City", "Oceanside", "Poway",
        "San Diego", "San Marcos", "Santee", "Solana Beach",
        "Vista", "Alpine", "Bonita", "Bonsall", "Borrego Springs",
        "Campo", "Fallbrook", "Jamul", "Julian", "La Jolla",
        "Lakeside", "Lincoln Acres", "Ramona", "Rancho Bernardo",
        "Rancho San Diego", "Rancho Santa Fe", "San Ysidro",
        "Spring Valley", "Valley Center", "Descanso", "Dulzura",
        "El Cajon", "Jacumba", "Potrero", "Tecate",
    },
    "San Francisco": {"San Francisco"},
    "San Joaquin": {
        "Escalon", "Lathrop", "Lodi", "Manteca", "Ripon", "Stockton",
        "Tracy", "Acampo", "Lockeford", "Morada", "Mountain House",
        "Peters", "Farmington", "Holt", "Thornton",
    },
    "San Luis Obispo": {
        "Arroyo Grande", "Atascadero", "Grover Beach",
        "Morro Bay", "Paso Robles", "Pismo Beach", "San Luis Obispo",
        "Avila Beach", "Baywood-Los Osos", "Cambria", "Cayucos",
        "Los Osos", "Nipomo", "Oceano", "Templeton",
        "El Paso de Robles", "Creston", "Santa Margarita",
    },
    "San Mateo": {
        "Atherton", "Belmont", "Brisbane", "Burlingame", "Colma",
        "Daly City", "East Palo Alto", "Foster City", "Half Moon Bay",
        "Hillsborough", "Menlo Park", "Millbrae", "Pacifica",
        "Portola Valley", "Redwood City", "San Bruno", "San Carlos",
        "San Mateo", "South San Francisco", "Woodside",
        "Baywood Park", "Broadmoor", "El Granada", "Emerald Lake Hills",
        "Kings Mountain", "Ladera", "Loma Mar", "Montara",
        "Moss Beach", "North Fair Oaks", "Princeton",
    },
    "Santa Barbara": {
        "Buellton", "Carpinteria", "Goleta", "Guadalupe", "Lompoc",
        "Santa Barbara", "Santa Maria", "Solvang", "Ballard",
        "Cuyama", "Isla Vista", "Los Alamos", "Los Olivos",
        "Mission Canyon", "Montecito", "Orcutt", "Santa Ynez",
        "Summerland", "Toro Canyon",
    },
    "Santa Clara": {
        "Campbell", "Cupertino", "Gilroy", "Los Altos", "Los Altos Hills",
        "Los Gatos", "Milpitas", "Monte Sereno", "Morgan Hill",
        "Mountain View", "Palo Alto", "San Jose", "Santa Clara",
        "Saratoga", "Sunnyvale", "Alviso", "Coyote", "Lexington Hills",
        "San Martin",
    },
    "Santa Cruz": {
        "Capitola", "Santa Cruz", "Scotts Valley", "Watsonville",
        "Aptos", "Ben Lomond", "Bonny Doon", "Boulder Creek",
        "Brookdale", "Corralitos", "Davenport", "Felton", "Freedom",
        "La Selva Beach", "Loma Prieta", "Soquel",
    },
    "Shasta": {
        "Anderson", "Redding", "Shasta Lake", "Burney", "Fall River Mills",
        "Cottonwood", "Igo", "Millville", "Palo Cedro", "Bella Vista",
        "Manton", "Shingletown",
    },
    "Sierra": {"Loyalton", "Downieville", "Sierraville", "Alleghany"},
    "Siskiyou": {
        "Dorris", "Dunsmuir", "Etna", "Fort Jones", "Greenview",
        "Happy Camp", "McCloud", "Mount Shasta", "Tulelake",
        "Weed", "Yreka", "Hornbrook", "Montague",
    },
    "Solano": {
        "Benicia", "Dixon", "Fairfield", "Rio Vista", "Suisun City",
        "Vacaville", "Vallejo", "Birds Landing", "Elmira", "Green Valley",
    },
    "Sonoma": {
        "Cloverdale", "Cotati", "Healdsburg", "Petaluma", "Rohnert Park",
        "Santa Rosa", "Sebastopol", "Sonoma", "Windsor",
        "Bodega Bay", "Boyes Hot Springs", "El Verano",
        "Fetters Hot Springs", "Forestville", "Geyserville", "Graton",
        "Guerneville", "Kenwood", "Monte Rio", "Occidental",
        "Penngrove", "Sea Ranch", "Timber Cove",
    },
    "Stanislaus": {
        "Ceres", "Hughson", "Modesto", "Newman", "Oakdale", "Patterson",
        "Riverbank", "Turlock", "Waterford", "Empire", "Keyes",
        "La Grange", "Salida", "Westley", "Denair", "Hickman",
    },
    "Sutter": {
        "Live Oak", "Marysville", "Yuba City", "East Nicolaus",
        "Nicolaus", "Robbins", "Meridian",
    },
    "Tehama": {
        "Corning", "Red Bluff", "Tehama", "Los Molinos", "Vina",
        "Gerber", "Proberta",
    },
    "Trinity": {"Weaverville", "Hayfork", "Ruth", "Trinity Center", "Lewiston"},
    "Tulare": {
        "Dinuba", "Exeter", "Farmersville", "Kingsburg", "Lindsay",
        "Porterville", "Tulare", "Visalia", "Woodlake",
        "Cutler", "East Porterville", "Goshen", "Ivanhoe",
        "Orosi", "Pixley", "Tipton", "Alpaugh", "Earlimart",
    },
    "Tuolumne": {
        "Sonora", "Tuolumne City", "Columbia", "Groveland",
        "Jamestown", "Twain Harte", "Tuolumne",
    },
    "Ventura": {
        "Camarillo", "Fillmore", "Moorpark", "Ojai", "Oxnard",
        "Port Hueneme", "Santa Paula", "Simi Valley", "Thousand Oaks",
        "Ventura", "El Rio", "Lake Sherwood", "Meiners Oaks",
        "Mira Monte", "Oak Park", "Oak View", "Piru",
        "Santa Rosa Valley", "Somis", "Newbury Park",
    },
    "Yolo": {
        "Davis", "West Sacramento", "Winters", "Woodland",
        "Clarksburg", "Esparto", "Knights Landing", "Madison",
        "Dunnigan", "Guinda", "Rumsey",
    },
    "Yuba": {
        "Marysville", "Wheatland", "Browns Valley",
        "Loma Rica", "Olivehurst", "Plumas Lake", "Smartsville",
        "Dobbins", "Oregon House",
    },
}

# Lowercase lookup for fast case-insensitive matching
_CA_COUNTY_CITIES_LOWER: dict[str, set[str]] = {
    county: {c.lower() for c in cities}
    for county, cities in CA_COUNTY_CITIES.items()
}

CALIFORNIA_COUNTIES: list[str] = sorted(CA_COUNTY_CITIES.keys())


def city_in_county(city: str, county: str) -> bool:
    """Return True if the given city is known to be in the given county."""
    if not city or not county:
        return True  # can't validate what we don't have
    county_set = _CA_COUNTY_CITIES_LOWER.get(county)
    if not county_set:
        return True  # unknown county — don't flag
    return city.strip().lower() in county_set
