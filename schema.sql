-- Enumerative and custom types
CREATE TYPE GenderType AS ENUM ('M', 'F', 'Unknown');
CREATE TYPE Ethnicity AS ENUM ('American', 'Caucasian', 'African', 'Asian', 'Hispanic', 'Ethiopian', 'Malaysian', 'Mongolian', 'Other');
CREATE TYPE DomainType AS ENUM ('real', 'discrete', 'string', 'time', 'float');
CREATE TYPE EventType AS ENUM ('Dati Anemia', 'Dati CKD-MBD', 'Dati Nutrizione', 'Ricovero', 'Seduta di dialisi');
CREATE DOMAIN String4PatientId AS CHAR(24);     -- 24 is maximum length of patient id in datalake
CREATE DOMAIN String4EventType AS VARCHAR(100);
CREATE DOMAIN String4VarName AS VARCHAR(67);    -- 67 is maximum length of variable name in datalake


-- Table Patient
CREATE TABLE Patient (
    patient_id String4PatientId PRIMARY KEY,     
    date_of_birth timestamp NOT NULL,
    date_of_death timestamp,                      
    date_of_critical_event timestamp,             -- first critical event in absolute (hospitalizations, vascular complications, death)
    gender GenderType,
    ethnicity Ethnicity,
    height REAL                                  -- NOTE: we don't have height for all patients
);

-- Table Measurements
CREATE TABLE Measurements (
    patient_ID String4PatientId NOT NULL,
    event_ID String4EventType,
    interval timestamp NOT NULL,
    event_type EventType,
    property VARCHAR(100) NOT NULL,
    value VARCHAR(100) NOT NULL,
    PRIMARY KEY (patient_ID, interval, property),               -- as in the original datalake DB
    CONSTRAINT patient_ref FOREIGN KEY(patient_ID) REFERENCES Patient(patient_id)
);

-- Table DependentProperties: properties that can be derived from the raw ones (e.g., we can derive "Score FAV" from "Score FAV raw" applying a transformation)
CREATE TABLE DependentProperties (
    patient_ID String4PatientId NOT NULL,
    interval timestamp NOT NULL,
    property VARCHAR(100) NOT NULL,
    value VARCHAR(100) NOT NULL,
    PRIMARY KEY (patient_ID, interval, property),
    CONSTRAINT patient_ref FOREIGN KEY(patient_ID) REFERENCES Patient(patient_id)
);


-- Table TimeVar 
CREATE TABLE TimeVar (
    var_id SERIAL PRIMARY KEY,
    var_name String4VarName NOT NULL UNIQUE,
    var_domain DomainType NOT NULL,
    lower_bound REAL NOT NULL,
    upper_bound REAL NOT NULL,
    average DOUBLE PRECISION NOT NULL,
    std_dev DOUBLE PRECISION NOT NULL
);


-- Table TimeSeries
CREATE TABLE TimeSeries (
    time timestamp NOT NULL,		                    --timestamp range of visit
    pid String4PatientId NOT NULL,			            --patient id
    tid int NOT NULL,				                    --variable id
    varvalue REAL NOT NULL,
	PRIMARY KEY (time, pid, tid),
    CONSTRAINT pid_ref FOREIGN KEY(pid) REFERENCES Patient(patient_id),
    CONSTRAINT tid_ref FOREIGN KEY(tid) REFERENCES TimeVar(var_id)
);
CREATE INDEX idx_timeseries_pid ON TimeSeries (pid);


-- Table for dataset of training and testing of neural network models. 
-- Each row corresponds to a patient at a specific timestamp, with the features (misure, aggregati_10, aggregati_20, aggregati_30) and the target variable (tte).
CREATE TABLE NN_Training_Dataset (
    timestamp timestamp NOT NULL,
    patient_id String4PatientId NOT NULL,
    misure DOUBLE PRECISION[] NOT NULL,       -- array of misure (raw measurements) for the patient at the given timestamp
    aggregati_10 DOUBLE PRECISION[] NOT NULL, -- array of averages over the last 10 sessions
    aggregati_20 DOUBLE PRECISION[] NOT NULL, -- array of averages over the last 20 sessions
    aggregati_30 DOUBLE PRECISION[] NOT NULL, -- array of averages over the last 30 sessions
    tte INTEGER NOT NULL,                     -- Time To Event in days (capped at 360)
    log_tte DOUBLE PRECISION NOT NULL,        -- log(TTE) capped
    tte_uncapped INTEGER NOT NULL,            -- Time To Event in days (uncapped)
    log_tte_uncapped DOUBLE PRECISION NOT NULL,-- log(TTE) uncapped
    
    PRIMARY KEY (timestamp, patient_id),
    CONSTRAINT patient_ref FOREIGN KEY(patient_id) REFERENCES Patient(patient_id)
);