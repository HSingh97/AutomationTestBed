// --------------------------------- VNX_atten.h -------------------------------------------
//
//	Include file for Linux LabBrick attenuator API V1.07
//
// (c) 2011-2020 by Vaunix Technology Corporation, all rights reserved
//
//	HME Version 1.0 based on RD Version 1.0 for Windows
//	RD	updated for LDA-302 family
//	RD	updated to support High Res LDA devices
//	RD	updated for multiple channel devices
//	RD  added new device type value list, new API functions, and 8 channel support
//  RD	added support for new devices and new multi-channel API functions
//	RD	added support for new devices and expandable LDA devices
//  JA  added support for new device of LDA-608V-4
//-----------------------------------------------------------------------------

//#include "libusb.h"
#include <libusb.h>

// Hardware type values by device name (used in lda[DeviceId].DevType)
#define LDA_102		1
#define LDA_602		2
#define LDA_302P_H	3
#define LDA_302P_1	4
#define LDA_302P_2	5
#define LDA_102_75	6
#define LDA_102E	7
#define LDA_602E	8
#define LDA_183		9
#define LDA_203		10
#define LDA_102EH	11
#define LDA_602EH	12
#define LDA_602Q	13
#define LDA_906V	14
#define LDA_133		15
#define LDA_5018	16
#define LDA_5040	17
#define LDA_906V_8	18
#define LDA_802EH	19
#define LDA_802Q	20
#define LDA_802_8	21
#define LDA_906V_4	22
#define LDA_8X1_752	23
#define LDA_908V	24
#define LDA_908V_4	25
#define LDA_908V_8	26
#define LDA_608V_4  27

#define VNX_MIN_DWELLTIME 1
#define STATUS_PROFILE_ACTIVE 0x80		// MASK: A profile is playing
#define STATUS_RF_ON 0x8				// MASK: The RF HW is on

// Bit masks and equates for the Sweep command byte (stored in Sweep_mode, and reported also in Status)
#define SWP_DIRECTION		0x04		// MASK: bit = 0 for sweep or ramp up, 1 for sweep or ramp down
#define SWP_CONTINUOUS		0x02		// MASK: bit = 1 for continuous sweeping
#define SWP_ONCE			0x01		// MASK: bit = 1 for single sweep
#define SWP_BIDIR			0x10		// MASK: bit = 0 for ramp style sweep, 1 for triangle style sweep

// ----------- Profile Control -----------
#define PROFILE_ONCE	1		// play the profile once
#define PROFILE_REPEAT	2		// play the profile repeatedly
#define PROFILE_OFF	0			// stop the profile

// HID report equates
#define HR_BLOCKSIZE 6			// size of the block of bytes buffer in our HID report


#define HID_REPORT_LENGTH 8 		// use an 8 byte report..

typedef struct
{
  char reportid;
  char status;
  char count;
  char byteblock[HR_BLOCKSIZE];
} HID_REPORT1;

typedef struct
{
  char reportid;
  char command;
  char count;
  char byteblock[HR_BLOCKSIZE];
} HID_REPORT_OUT;

// Misc commands to send to the device
// For the input reports the first byte is the status, for the output it is the command. The high bit sets the
// direction.
//
//	count is the number of valid bytes in the byteblock array
// 	byteblock is an array of bytes which make up the value of the command's argument or arguments.
//
// For result reports the command portion of the status byte is equal to the command sent if the command was successful.
// status byte layout:

// Bit0 - Bit5 = command result, equal to command if everything went well
// Bit6 = --reserved--
// Bit7 = --reserved--

// All sweep related data items are DWORD (unsigned) quantities, stored in normal Microsoft byte order.
// Dwell time is a DWORD (unsigned)


// Misc commands to send to the device

#define VNX_SET			0x80
#define VNX_GET			0x00	// the set and get bits are or'd into the msb of the command byte


// ---------------------- Attenuator commands ------------------------
#define VNX_PWR			0x0D	// power output setting, relative to calibrated value - adds to calibrated
					// attenuator setting. It is a byte, with the attenuation expressed in HW
					// specific steps.

#define VNX_FREQUENCY		0x04	// working frequency in 100Khz units
// ----------------- Attenuator ramp commands ------------------------
#define VNX_SWEEP		0x09	// command to start/stop sweep, data = 01 for single sweep, 00 to stop
					// sweeping, and 02 for continuous sweeping.

#define VNX_RFMUTE		0x0A	// enable or disable RF output, byte = 01 to enable, 00 to disable

#define VNX_ASTART		0x30	// initial value for attenuation ramp

#define VNX_ASTOP		0x31	// final value for attenuation ramp

#define VNX_ASTEP		0x32	// step size for attenuation ramp
#define VNX_ASTEP2		0x38	// step size for the second phase of the ramp

#define VNX_ADWELL		0x33	// dwell time for each attenuation step
#define VNX_ADWELL2		0x37	// dwell time for the second phase of the ramp

#define VNX_AIDLE		0x36	// idle time between attenuation ramps in milliseconds
#define VNX_AHOLD		0x39	// hold time between phase 1 and 2

#define VNX_SETPROFILE		0x3A	// set/get profile values, first byte is unused
					// second data byte is the index (0 based) (for non-HiRes devices)
					// the third is the attenuation value for that profile entry

#define VNX_PROFILECOUNT	0x3B	// number of elements in the profile, 1 to PROFILE_MAX = 100
#define VNX_PROFILEDWELL	0x3C	// dwell time for each profile element
#define VNX_PROFILEIDLE		0x3D	// idle time at the end of each repeating profile

#define VNX_SAVEPAR		0x0C	// command to save user parameters to flash, data bytes must be
					// set to 0x42, 0x55, 0x31 as a key to enable the flash update
					// all of the above settings are saved (RF Mute State, Attenuation,
					// sweep parameters, etc.

#define VNX_MINATTEN		0x34	// get the minimum attenuation level which is 0 for every case now

#define VNX_MAXATTEN		0x35	// get the maximum attenuation level which is 252 or 63db for both products now

#define VNX_GETSERNUM		0x1F	// get the serial number, value is a DWORD

#define VNX_MODELNAME		0x22	// get (no set allowed) the device's model name string -- last 6 chars only

#define VNX_DEFAULTS		0x0F	// restore all settings to factory default
					// ASTART = 0 = MINATTEN, ASTOP = MAXATTEN
					// ADWELL = 1000 = 1 second, ASTEP = 2 = .5db, AIDLE = 0

// ------------------------ Hi Res Attenuator Commands --------------------------------
#define VNX_MINFREQUENCY	0x20	// get (no set allowed) the device's minimum working frequency
#define VNX_MAXFREQUENCY	0x21	// get (no set allowed) the device's maximum working frequency

//------------------------- Quad Attenuator Commands-----------------------------------
#define VNX_CHANNEL         	0x54    // set the channel
//------------------------- Status Report ID Byte -------------------------------------
#define VNX_STATUS		0x0E	// Not really a command, but the status byte value for periodic status reports.
#define VNX_HRSTATUS		0x52	// status report used by HiRes (1 and 4 channel)
#define VNX_HR8STATUS		0x55	// status report used by HiRes 8 channel devices

// ----------- Global Equates ------------
#define MAXDEVICES 64
#define MAX_MODELNAME 32
#define PROFILE_MAX 100		// Older non-hires attenuators can store 100 profile elements in their eeprom
#define PROFILE_MAX_RAM 1000	// New FW V2.x based attenuators have a 1000 element RAM profile buffer
#define PROFILE_MAX_HR 50	// HiRes Attenuators only save 50 elements in their eeprom
#define CHANNEL_MAX 64		// The largest device today has 64 channels with a full set of expansion modules

// ----------- Data Types ----------------
#define DEVID unsigned int
#define PROFILE_EMPTY 0xFFFFFFFF // we use this as our marker for an empty entry in the profile

typedef struct
{
  //  Global device variables
  int DevType;
  int MinFrequency;
  int MaxFrequency;
  int MinAttenuation;
  int MaxAttenuation;			// maximum attenuation in .05 db units
  int MinAttenStep;				// replaces DevResolution, smallest attenuation step in .05 db units
  int UnitScale;				// size of hardware unit in .05 db units ( .25db = 5, .05db = 1. .5db = 10, 1db = 20)
  int NumChannels;				// the number of channels the device has including expansion channels
  bool Expandable;				// true for devices that can have expansion channels
  int ProfileMaxLength;			// 50, 100, or 1000 depending on the device
  int ProfileMaxSaved;			// the maximum length of the profile that can be saved in eeprom, 50 for HiRes devices, 100 for other devices
  volatile int Channel;			// the current channel number
  volatile int ChMask;			// our channel in bitmask form (0x01 to 0x80 for single channels)
  volatile int GlobalChannel;	// the current global channel using a zero based index
  volatile int BaseChannels;	// the total number of channels in the base LDA device
  int SerialNumber;
  char ModelName[MAX_MODELNAME];
  unsigned int FrameNumber;
  //  Per channel variables
  volatile int DevStatus[CHANNEL_MAX];
  volatile int WorkingFrequency[CHANNEL_MAX];
  volatile int Attenuation[CHANNEL_MAX];				// in .05db units
  volatile int RampStart[CHANNEL_MAX];
  volatile int RampStop[CHANNEL_MAX];
  volatile int AttenuationStep[CHANNEL_MAX];			// ramp step size for the first phase of the ramp
  volatile int AttenuationStep2[CHANNEL_MAX];			// ramp step size for second phase of the ramp
  volatile int DwellTime[CHANNEL_MAX];
  volatile int DwellTime2[CHANNEL_MAX];
  volatile int IdleTime[CHANNEL_MAX];
  volatile int HoldTime[CHANNEL_MAX];
  volatile int ProfileCount[CHANNEL_MAX];
  volatile int ProfileIndex[CHANNEL_MAX];
  volatile int ProfileDwellTime[CHANNEL_MAX];
  volatile int ProfileIdleTime[CHANNEL_MAX];
  volatile int Modebits[CHANNEL_MAX];
  volatile int CachedProfileValue[CHANNEL_MAX];			// we only cache the last used profile entry now to avoid allocating a large but rarely used memory buffer
														// we don't use a malloc/free strategy to avoid memory leak issues if an application fails to close a device
														// The upper 16 bits holds the index, the lower 16 bits holds the value. Packing them as a pair into one int avoids possible race conditions
														// due to a non-atomic read of the structure if they were separate variables.

  // Internal variables used to identify and manage the hardware
  unsigned int idVendor;
  unsigned int idProduct;
  unsigned int idType;
  int BusAddress;
  int Endpoint;
  char Serialstr[16];
  volatile char thread_command;
  char sndbuff[8];
  char rcvbuff[24];
  volatile char decodewatch;
  int MyDevID;
  libusb_device_handle *DevHandle;

} LDAPARAMS;

// ----------- Mode Bit Masks ------------

#define MODE_RFON 		0x00000010 	// bit is 1 for RF on, 0 if RF is off
#define MODE_INTREF 	0x00000020 	// bit is 1 for internal osc., 0 for external reference
#define MODE_SWEEP 		0x0000000F 	// bottom 4 bits are used to keep the sweep control bits

// ----------- Profile Control -----------
#define PROFILE_ONCE    1                // play the profile once
#define PROFILE_REPEAT  2                // play the profile repeatedly
#define PROFILE_OFF     0                // stop the profile

// ----------- Command Equates -----------

#define BYTE unsigned char

// Status returns for commands
#define LVSTATUS unsigned int

#define STATUS_OK 0
#define INVALID_DEVID       	0x80000000
#define BAD_PARAMETER  			  0x80010000 	// out of range input -- frequency outside min/max etc.
#define BAD_HID_IO     			  0x80020000
#define DEVICE_NOT_READY  		0x80030000 	// device isn't open, no handle, etc.
#define FEATURE_NOT_SUPPORTED	0x80040000	// the selected Lab Brick does not support this function
                                          // Profiles and Bi-directional ramps are only supported in
                                          // newer LDA models
#define DATA_UNAVAILABLE      0x80050000  // the requested data item is unavailable
                                          // usually due to a ramp or profile where reporting times exceed timeout limits


// Some of these are global in that they describe the hardware and some are
// channel specific.

// Status returns for DevStatus
#define INVALID_DEVID 		0x80000000 	// MSB is set if the device ID is invalid [global]
#define DEV_CONNECTED 		0x00000001 	// LSB is set if a device is connected [global]
#define DEV_OPENED 			0x00000002 	// set if the device is opened [global]
#define SWP_ACTIVE 			0x00000004 	// set if the device is sweeping
#define SWP_UP 				0x00000008 	// set if the device is sweeping up in frequency
#define SWP_REPEAT 			0x00000010 	// set if the device is in continuous sweep mode
#define SWP_BIDIRECTIONAL	0x00000020	// set if the device is in bi-directional ramp mode
#define PROFILE_ACTIVE		0x00000040	// set if a profile is playing

// Internal values in DevStatus
#define DEV_LOCKED   		0x00002000 	// set if we don't want read thread updates of the device parameters
#define DEV_RDTHREAD   		0x00004000 	// set when the read thread is running [global]
#define DEV_V2FEATURES		0x00008000	// set for devices with V2 feature sets [global]
#define DEV_HIRES			0x00010000	// set for HiRes devices [global]

// Feature bits for the feature DWORD
#define DEFAULT_FEATURES	0x00000000
#define HAS_BIDIR_RAMPS		0x00000001
#define HAS_PROFILES		0x00000002
#define HAS_HIRES			0x00000004
#define HAS_4CHANNELS		0x00000008
#define HAS_8CHANNELS       0x00000010
#define HAS_LONG_PROFILE	0x00000020

void fnLDA_Init(void);

void fnLDA_SetTraceLevel(int tracelevel, int IOtracelevel, bool verbose);
void fnLDA_SetTestMode(bool testmode);
int fnLDA_GetNumDevices();
int fnLDA_GetDevInfo(DEVID *ActiveDevices);
int fnLDA_GetModelName(DEVID deviceID, char *ModelName);
int fnLDA_InitDevice(DEVID deviceID);
int fnLDA_CloseDevice(DEVID deviceID);
int fnLDA_GetSerialNumber(DEVID deviceID);
int fnLDA_GetLibVersion();
int fnLDA_GetDeviceStatus(DEVID deviceID);

LVSTATUS fnLDA_SetChannel(DEVID deviceID, int channel);
LVSTATUS fnLDA_SetWorkingFrequency(DEVID deviceID, int frequency);
LVSTATUS fnLDA_SetAttenuation(DEVID deviceID, int attenuation);
LVSTATUS fnLDA_SetAttenuationQ(DEVID deviceID, int attenuation, int channel);

LVSTATUS fnLDA_SetRampStart(DEVID deviceID, int rampstart);
LVSTATUS fnLDA_SetRampEnd(DEVID deviceID, int rampstop);
LVSTATUS fnLDA_SetAttenuationStep(DEVID deviceID, int attenuationstep);
LVSTATUS fnLDA_SetAttenuationStepTwo(DEVID deviceID, int attenuationstep2);
LVSTATUS fnLDA_SetDwellTime(DEVID deviceID, int dwelltime);
LVSTATUS fnLDA_SetDwellTimeTwo(DEVID deviceID, int dwelltime2);
LVSTATUS fnLDA_SetIdleTime(DEVID deviceID, int idletime);
LVSTATUS fnLDA_SetHoldTime(DEVID deviceID, int holdtime);

LVSTATUS fnLDA_SetProfileElement(DEVID deviceID, int index, int attenuation);
LVSTATUS fnLDA_SetProfileCount(DEVID deviceID, int profilecount);
LVSTATUS fnLDA_SetProfileIdleTime(DEVID deviceID, int idletime);
LVSTATUS fnLDA_SetProfileDwellTime(DEVID deviceID, int dwelltime);
LVSTATUS fnLDA_StartProfile(DEVID deviceID, int mode);
LVSTATUS fnLDA_StartProfileMC(DEVID deviceID, int mode, int chmask, bool delayed);

LVSTATUS fnLDA_SetRFOn(DEVID deviceID, bool on);

LVSTATUS fnLDA_SetRampDirection(DEVID deviceID, bool up);
LVSTATUS fnLDA_SetRampMode(DEVID deviceID, bool mode);
LVSTATUS fnLDA_SetRampBidirectional(DEVID deviceID, bool bidir_enable);
LVSTATUS fnLDA_StartRamp(DEVID deviceID, bool go);
LVSTATUS fnLDA_StartRampMC(DEVID deviceID, int mode, int chmask, bool deferred);

LVSTATUS fnLDA_SaveSettings(DEVID deviceID);

int fnLDA_GetWorkingFrequency(DEVID deviceID);
int fnLDA_GetMinWorkingFrequency(DEVID deviceID);
int fnLDA_GetMaxWorkingFrequency(DEVID deviceID);

int fnLDA_GetAttenuation(DEVID deviceID);
int fnLDA_GetRampStart(DEVID deviceID);
int fnLDA_GetRampEnd(DEVID deviceID);
int fnLDA_GetDwellTime(DEVID deviceID);
int fnLDA_GetDwellTimeTwo(DEVID deviceID);
int fnLDA_GetIdleTime(DEVID deviceID);
int fnLDA_GetHoldTime(DEVID deviceID);

int fnLDA_GetAttenuationStep(DEVID deviceID);
int fnLDA_GetAttenuationStepTwo(DEVID deviceID);
int fnLDA_GetRF_On(DEVID deviceID);

int fnLDA_GetProfileElement(DEVID deviceID, int index);
int fnLDA_GetProfileCount(DEVID deviceID);
int fnLDA_GetProfileDwellTime(DEVID deviceID);
int fnLDA_GetProfileIdleTime(DEVID deviceID);
int fnLDA_GetProfileIndex(DEVID deviceID);


int fnLDA_GetMaxAttenuation(DEVID deviceID);
int fnLDA_GetMinAttenuation(DEVID deviceID);
int fnLDA_GetMinAttenStep(DEVID deviceID);
int fnLDA_GetDevResolution(DEVID deviceID);		// use fnLDA_GetMinAttenStep instead
int fnLDA_GetFeatures(DEVID deviceID);
int fnLDA_GetNumChannels(DEVID deviceID);
int fnLDA_GetProfileMaxLength(DEVID deviceID);

char* fnLDA_perror(LVSTATUS status);
char* fnLDA_LibVersion(void);
