// -------- High Resolution LDA Linux Library ------------
//
// RD 6/9/2017
// HME 06/13/2018   Started rewrite for libusb 1.0
// HME 08/19/2018   Restarted rewrite for libusb 1.0 - tagged as 0.99
// HME 08/20/2018   Removed conditional compile
// HME 08/24/2018   Added RD mutichannel support
// HME 12/22/2018   Corrected missing 602Q string - changed to 1.01 (skipping 1.00)
// HME 01/04/2019   Syntax fixes for C99 compatibility
// HME 03/29/2019   Made changes to the default ranges for the 602Q
// HME 04/03/2019   Added the 906V from Sean Scanlon's suggested changes
// RD  04/24/2019	Revised to fix FC19 segment fault in libusb_interrupt_transfer,
//					changing endpoints in HW, and device detection and open/close process
// RD  05/09/2019	This version has <libusb.h> to use the system installed version instead of a local copy
// HME 05/13/2019   Added in placeholders for missing functions
// RD  05/17/2019	Added functions
// RD  11/24/2019	Added multi-channel functions, along with support for new devices and long profiles
// RD  12/11/2019	Added timer based USB transaction throttle to avoid overrunning the devices on certain
//					host controllers.
// RD  12/12/2019	Reduced catnap times during reads to improve performance
// RD  8/16/2020	Added support for expandable LDA devices with up to 64 channels
// RD  9/21/2020	Revised the profile caching strategy, only the last used profile entry is cached now
// ----------------------------------------------------------------------------
// This library uses .05db units for attenuation values, so 10db is represented by 200. (multiply by 20 to convert db to api units)

//#include "libusb.h"	// use a local copy, helpful on systems with legacy versions of libusb-1.0
#include <libusb.h>		// rely on the system installed copy
#include <linux/hid.h>	/* AK: Changed include for modern linux. */
#include <stdbool.h>	/* AK: Added include for 'bool' type */
#include <stdint.h>		// RD: used to avoid compiler cast warnings int -> void
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <string.h>
#include <unistd.h>
#include <pthread.h>
#include <time.h>
#include <errno.h>
#include "LDAhid.h"

#define FALSE 0
#define TRUE 1			// RD -- aligned with ISO standard for bool type

#define PACKET_CTRL_LEN 8
#define PACKET_INT_LEN 8
#define INTERFACE 0
#define ENDPOINT_INT_IN1 0x81
#define ENDPOINT_INT_IN2 0x82
#define TIMEOUT 200   //500		RD 9/23/20 changed to 200 to make it shorter than the close thread timeout

#define LANGID 0x0409
#define USE_MISC 1  // HME: Makes Unistd.h happy

//#define LIBVER "0.98"	// RD:	added support for more devices, including High Res attenuators,
												//	internal representation of attenuation is now in .05 db units
//#define LIBVER "0.99" // HME: switched to libusb 1.0
//#define LIBVER "1.05" // Has extensive mods over 1.04
#define LIBVER "1.09"

#define LDA_DLLVERSION 0x109		// we return an integer representation of the version with one byte each for major and minor version




void *brick_handler_function (void *ptr);
int SetChannel(DEVID deviceID, int channel);


/* ----------------------------------------------------------------- */
/* globals we'll be using at runtime */
char errmsg[32]; 			// For the status->string converter

bool bVerbose = FALSE; 		// True to generate debug oriented printf output during device enumeration
int Trace_Out = 0;          // variable to control how much tracing to do
int IO_Trace = 0;           // used for tracing control during device init and I/O operations

bool TestMode = TRUE; 		// if TestMode is true we fake it -- no HW access
							// TestMode defaults to FALSE for production builds

bool Did_libusb_init = FALSE;	// Flag used to prevent multiple calls to libusb_init, and to enable de-init when no devices are open

LDAPARAMS lda [MAXDEVICES]; // an array of structures each of which holds the info for a given
							// device. The DeviceID is the index into the array. Devices may come and go
							// so there is no guarantee that the active elements are contiguous
							// lda[0] is used as a temporary device info structure so we actually
							// only support MAXDEVICES-1 devices.

// we use timers for our read timeout, and to throttle the sending of USB commands
time_t starttime, currtime;
bool HasHRTimer = FALSE;
struct timespec mtimer_CTime, mtimer_LastSend;


// product names
const char sVNX1[] = "LDA-102";
const char sVNX2[] = "LDA-602";
const char sVNX3[] = "LDA-302P-H";
const char sVNX4[] = "LDA-302P-1";
const char sVNX5[] = "LDA-302P-2";
const char sVNX6[] = "LDA-102-75";
const char sVNX7[] = "LDA-102E";
const char sVNX8[] = "LDA-602E";
const char sVNX9[] = "LDA-183";
const char sVNX10[] = "LDA-203";
const char sVNX11[] = "LDA-102EH";
const char sVNX12[] = "LDA-602EH";
const char sVNX13[] = "LDA-602Q ";
const char sVNX14[] = "LDA-906V";
const char sVNX15[] = "LDA-133";
const char sVNX16[] = "LDA-5018";
const char sVNX17[] = "LDA-5040";
const char sVNX18[] = "LDA-906V-8";
const char sVNX19[] = "LDA-802EH";
const char sVNX20[] = "LDA-802Q";
const char sVNX21[] = "LDA-802-8";
const char sVNX22[] = "LDA-906V-4";
const char sVNX23[] = "LDA-8X1-752";
const char sVNX24[] = "LDA-908V";
const char sVNX25[] = "LDA-908V-4";
const char sVNX26[] = "LDA-908V-8";
const char sVNX27[] = "LDA-608V-4";


// device VID and PID
const unsigned short devVID = 0x041f; // device VID for Vaunix Devices

const unsigned short LDA_Pids[28] = {
							0x0000,  // unused
							0x1207,  // device PID for Vaunix LDA-102
							0x1208,  // device PID for Vaunix LDA-602
							0x120D,  // device PID for Vaunix LDA-302P-H
							0x120E,  // device PID for Vaunix LDA-302P-1
							0x120F,  // device PID for Vaunix LDA-302P-2
							0x1210,  // device PID for Vaunix LDA-102-75
							0x120B,  // device PID for Vaunix LDA-102E
							0x120C,  // device PID for Vaunix LDA-602E
							0x1211,  // device PID for Vaunix LDA-183
							0x1212,  // device PID for Vaunix LDA-203
							0x1213,  // device PID for Vaunix LDA-102EH	RD: added 6/2017. This is also the LDA-602Q if we see it as a 4-channel
							0x1214,  // device PID for Vaunix LDA-602EH	RD: added 6/2017
							0x1215,  // device PID for Vaunix LDA-602Q 	HME: 12/22/18
							0x1216,  // device PID for Vaunix LDA-906V 	HME: 04/03/19
							0x1217,  // device PID for Vaunix LDA-133
							0x1218,  // device PID for Vaunix LDA-5018
							0x1219,  // device PID for Vaunix LDA-5040
							0x121A,	 // device PID for Vaunix LDA-906V-8	RD: added 4/2019
							0x121B,	 // device PID for Vaunix LDA-802EH
							0x121C,  // device PID for Vaunix LDA-802Q
							0x121D,  // device PID for Vaunix LDA-802-8
							0x121E,  // device PID for Vaunix LDA-906V-4
							0x121F,  // device PID for Vaunix LDA-8X1-752
							0x1260,  // device PID for Vaunix LDA-908V
							0x1262,  // device PID for Vaunix LDA-908V-4
							0x1263,  // device PID for the Vaunix LDA-908V-8
							0x1266}; // device PID for the Vaunix LDA-608V-4


/* stuff for the threads */
pthread_t threads[MAXDEVICES];

#define THREAD_IDLE 0
#define THREAD_START 1
#define THREAD_EXIT 3
#define THREAD_DEAD 4
#define THREAD_ERROR -1

// variables used to manage libusb 1.0 interaction
unsigned busnum = 0, devaddr = 0, _busnum, _devaddr;
libusb_device *dev, **devs;
libusb_device_handle *device = NULL;
struct libusb_device_descriptor desc;
libusb_context *LDA_ctx = NULL;


// to force LDA library debugging messages choose the following definitions
//#define DEBUG_OUT 0  	/* set this to 1 in order to see debugging output, 2 for a ton of output, or 3 for many tons */
//#define FORCE_LUSB_DEBUG 1

// otherwise use these definitions to allow application level control of the debug messages
#define DEBUG_OUT Trace_Out



/// ---------- Functions ----------------

/* usleep is deprecated, so this is a function using nanosleep() */
void catnap(long naptime) {
  // naptime comes in as the number of ms we want. 20 = 20ms
  struct timespec sleepytime;
   sleepytime.tv_sec = 0;
   sleepytime.tv_nsec = naptime * 1000000L;

   nanosleep(&sleepytime , NULL);
}

// --------------- Device IO support functions ----------------------------

bool CheckDeviceOpen(DEVID deviceID) {
  if (TestMode) return TRUE;// in test mode all devices are always available
  // Even for multichannel devices, we can use channel 0 to know if the device is open
  if ((lda[deviceID].DevStatus[0] & DEV_OPENED) && (deviceID != 0))
    return TRUE;
  else
    return FALSE;
}

// ------------------------------------------------------------------------
bool DevNotLocked(DEVID deviceID) {
  if (TestMode) return TRUE;// this shouldn't happen, but just in case...
  if (!(lda[deviceID].DevStatus[lda[deviceID].Channel] & DEV_LOCKED))
    return TRUE;// we return TRUE if the device is not locked!
  else
    return FALSE;
}

// ------------------------------------------------------------------------
void LockDev(DEVID deviceID, bool lock) {
  if (TestMode) return;// this shouldn't happen, but just in case...
  if (lock) {
    lda[deviceID].DevStatus[lda[deviceID].Channel] = lda[deviceID].DevStatus[lda[deviceID].Channel] | DEV_LOCKED;
    return;
  } else {
    lda[deviceID].DevStatus[lda[deviceID].Channel] = lda[deviceID].DevStatus[lda[deviceID].Channel] & ~DEV_LOCKED;
    return;
  }
}

// -- Clear the variables we cache to their unknown state --
void ClearDevCache(DEVID deviceID, int num_channels) {
	int ch;
	int i;

	if (num_channels > CHANNEL_MAX) num_channels = CHANNEL_MAX;	// protect our arrays

	for (ch = 0; ch < num_channels; ch++) {
		lda[deviceID].Attenuation[ch] = -1;			// we mark the attenuation as unknown to start
		lda[deviceID].WorkingFrequency[ch] = -1;	// and everybody else...
		lda[deviceID].RampStart[ch] = -1;
		lda[deviceID].RampStop[ch] = -1;
		lda[deviceID].AttenuationStep[ch] = -1;
		lda[deviceID].AttenuationStep2[ch] = -1;
		lda[deviceID].DwellTime[ch] = -1;
		lda[deviceID].DwellTime2[ch] = -1;
		lda[deviceID].IdleTime[ch] = -1;
		lda[deviceID].HoldTime[ch] = -1;
		lda[deviceID].Modebits[ch] = 0;				// Modebits aren't directly readable, but clearing them here for convenience
		lda[deviceID].ProfileIndex[ch] = -1;
		lda[deviceID].ProfileDwellTime[ch] = -1;
		lda[deviceID].ProfileIdleTime[ch] = -1;
		lda[deviceID].ProfileCount[ch] = -1;
		lda[deviceID].CachedProfileValue[ch] = -1;	// The int holds the index and the value, we init the whole thing to 0xFFFFFFFF to indicate it is empty.

		}
	}

/* A function to display the status as string */
char* fnLDA_perror(LVSTATUS status) {
  strcpy(errmsg, "STATUS_OK");
  if (BAD_PARAMETER == status) strcpy(errmsg, "BAD_PARAMETER");
  if (BAD_HID_IO == status) strcpy(errmsg, "BAD_HID_IO");
  if (DEVICE_NOT_READY == status) strcpy(errmsg, "DEVICE_NOT_READY");

  // Status returns for DevStatus
  if (INVALID_DEVID == status) strcpy(errmsg, "INVALID_DEVID");
  if (DEV_CONNECTED == status) strcpy(errmsg, "DEV_CONNECTED");
  if (DEV_OPENED == status) strcpy(errmsg, "DEV_OPENED");
  if (SWP_ACTIVE == status) strcpy(errmsg,  "SWP_ACTIVE");
  if (SWP_UP == status) strcpy(errmsg, "SWP_UP");
  if (SWP_REPEAT == status) strcpy(errmsg, "SWP_REPEAT");

  return errmsg;

}

bool CheckV2Features(DEVID deviceID) {
  // Even for multichannel devices, we can use channel 0 to know if the device is V2
  if (lda[deviceID].DevStatus[0] & DEV_V2FEATURES)
    {
      return TRUE;
    }
  else return FALSE;
}


bool CheckHiRes(DEVID deviceID) {
  // Even for multichannel devices, we can use channel 0 to know if the device is HiRes
  if (lda[deviceID].DevStatus[0] & DEV_HIRES)
    {
      return TRUE;
    }
  else return FALSE;
}


char LibVersion[] = LIBVER;
char* fnLDA_LibVersion(void) {
  return LibVersion;
}

// functions based on hid_io.cpp
// RD 4-2019 modified to do the libusb interaction on the API thread instead of in the read thread and return better status
LVSTATUS VNXOpenDevice(DEVID deviceID) {

	int usb_status;
	int retries;
	int cnt, cntidx;
	libusb_device **devlist, *dev ;
	struct libusb_device_descriptor desc;
	int HWBusAddress = 0;


  if (!(lda[deviceID].DevStatus[0] & DEV_CONNECTED))	// we can't open a device that isn't there!
    return DEVICE_NOT_READY;

  if (lda[deviceID].DevStatus[0] & DEV_OPENED)			// the device is already open, BAD_PARAMETER gives a clue that it isn't just missing or disconnected
	return BAD_PARAMETER;

  if (!Did_libusb_init){
	usb_status = libusb_init(&LDA_ctx);		// we may need to re-init since we call libusb_exit when a user closes the last device
	if (usb_status != 0) return BAD_HID_IO;
	Did_libusb_init = TRUE;
  }

  // we'll open the device. First we have to locate it by using its physical location where we enumerated it

	cnt = libusb_get_device_list(LDA_ctx, &devlist);

    for (cntidx=0; cntidx<cnt; cntidx++) {
		dev = devlist[cntidx];
		if (DEBUG_OUT > 2) printf("Device Search: cntidx=%d cnt=%d dev=%p\r\n", cntidx, cnt, (void *)dev);


		usb_status = libusb_get_device_descriptor(dev, &desc);

		// gather the USB address for this device so we can find it in the future
		HWBusAddress = ((libusb_get_bus_number(dev) << 8) | (libusb_get_device_address(dev)));

		if (HWBusAddress == lda[deviceID].BusAddress) {

			// we found the device, lets open it so the user can interact with it
			if (DEBUG_OUT > 2) printf("Opening LDA device %04x:%04x at bus address %04x\r\n",
					desc.idVendor,
					desc.idProduct,
					HWBusAddress);

			usb_status = libusb_open(dev, &lda[deviceID].DevHandle);		// RD 4-2019 the device handle is now stored as a part of the LDA structure

			if ((DEBUG_OUT > 2) && (usb_status != 0)) printf("  libusb_open returned error status = %x\r\n", usb_status);

			if (usb_status) {

				// our device opened failed, not much we can do about that except clean up and let the caller know
				libusb_free_device_list(devlist, 1);
				return DEVICE_NOT_READY;
			}

			if (DEBUG_OUT > 1) printf("  Detaching kernel driver %04x:%04x\r\n", lda[deviceID].idVendor, lda[deviceID].idProduct);
			usb_status = libusb_detach_kernel_driver(lda[deviceID].DevHandle, 0);

			if ((DEBUG_OUT > 2) && (usb_status != 0)) printf("  Detaching kernel driver returned error status = %x\r\n", usb_status);

			// In theory the libusb kernel driver might already be attached, so we ignore the LIBUSB_ERROR_NOT_FOUND case
			if ((usb_status != 0) && (usb_status != LIBUSB_ERROR_NOT_FOUND)) {

			libusb_free_device_list(devlist, 1);
			// we might have a disconnected device, so check for that
				if (usb_status == LIBUSB_ERROR_NO_DEVICE) {
					lda[deviceID].DevStatus[0] = lda[deviceID].DevStatus[0] & ~DEV_CONNECTED;
					return DEVICE_NOT_READY;
				}

				return BAD_HID_IO;		// not necessarily IO layer, but something failed in the kernel stack below us
			}

			if (DEBUG_OUT > 2) printf("  Setting configuration %04x:%04x\r\n", lda[deviceID].idVendor, lda[deviceID].idProduct);
			usb_status = libusb_set_configuration (lda[deviceID].DevHandle, 1);

			if (DEBUG_OUT > 2) printf ("  successfully set configuration: %s\n", usb_status ? "failed" : "passed");

			if (usb_status != 0) {

				libusb_free_device_list(devlist, 1);
				// we might have a disconnected device, so check for that
				if (usb_status == LIBUSB_ERROR_NO_DEVICE) {
					lda[deviceID].DevStatus[0] = lda[deviceID].DevStatus[0] & ~DEV_CONNECTED;
					return DEVICE_NOT_READY;
				}

				return BAD_HID_IO;		// not necessarily IO layer, but something failed in the kernel stack below us
			}

			usb_status = libusb_claim_interface (lda[deviceID].DevHandle, 0);

			if (DEBUG_OUT > 1) printf ("claim interface: %s\n", usb_status ? "failed" : "passed");

			if (usb_status != 0) {

				libusb_free_device_list(devlist, 1);
				// we might have a disconnected device, so check for that
				if (usb_status == LIBUSB_ERROR_NO_DEVICE) {
					lda[deviceID].DevStatus[0] = lda[deviceID].DevStatus[0] & ~DEV_CONNECTED;
					return DEVICE_NOT_READY;
				}

				return BAD_HID_IO;		// not necessarily IO layer, but something failed in the kernel stack below us
			}

			// now we can start the read thread
			if (DEBUG_OUT > 2) printf("Starting thread %d\r\n", deviceID);
			lda[deviceID].thread_command = THREAD_START; /* open device and start processing */
			pthread_create(&threads[deviceID], NULL, brick_handler_function, (void*)(uintptr_t)deviceID);

			// wait here for the thread to start up and get out of the startup state
			retries = 10;
			while (retries && (lda[deviceID].thread_command == THREAD_START)) {
				catnap(100);  /* wait 100 ms */
				retries--;
			}

			if (DEBUG_OUT > 2) {
				printf("Read Thread Started, thread status = %d\r\n", lda[deviceID].thread_command);
			}

			// RSD Test
			catnap(100);

			if (lda[deviceID].thread_command == THREAD_START) {

				// for some reason our thread failed to startup and run, not much we can do but bail out
				libusb_free_device_list(devlist, 1);
				lda[deviceID].thread_command = THREAD_EXIT;

				// wait here for the thread to exit on its own
				retries = 10;
				while (retries && (lda[deviceID].thread_command != THREAD_DEAD)) {
					catnap(100);  /* wait 100 ms */
					retries--;
				}

				// the thread didn't exit on its own, so just cancel it
				if (lda[deviceID].thread_command != THREAD_DEAD) {
					pthread_cancel(threads[deviceID]);
				}

				return BAD_HID_IO;

			}

			// Even for multichannel devices, we use channel 0 to know if the device is open, so set the device open flag
			lda[deviceID].DevStatus[0] = lda[deviceID].DevStatus[0] | DEV_OPENED;

			// we only open one device, so we are done now
			libusb_free_device_list(devlist, 1);
			return STATUS_OK;

		}	// end of matching bus address case
	}
  // we failed to find a matching device if we get here
  libusb_free_device_list(devlist, 1);
  return DEVICE_NOT_READY;
}


// With the arrival of the expandable LDA devices it is easier to separate out the mask to channel function used for status reports
// from the general case used for responses
// Bit masks are used to identify the channel within either an 8 or 4 channel range, set by the width parameter
//

int BitMaskToChannel(int tid, int width, int mask)
{
	int channel;

	// We trap this case
	if (lda[tid].NumChannels == 1) return 0; // single channel devices only have channel 0.

	// the mask can be either 4 bits wide or 8 bits wide
	if (width == 4) {
		mask &= 0x0F;												  // clear out any stray bits for a 4 channel mask
	}
	else if (width == 8) {
		mask &= 0x0FF;											  // clear out any stray bits for an 8 channel mask
	}
	else {
		return 0;														  // we should never see this case
	}

	for (channel = 0; channel < width; channel++)
	{
		if (mask & 0x01) return channel;
		mask >>= 1;
	}

	return 0;								// hopefully we never see this failure case...
}


// -- Modified by RD 8/2020 to use the global channel setting when we have a device with more than 8 channels
//  for these devices we have to infer the source of the response and assume it is from our current channel
//
int MaskToChannel(int tid, int bank, int mask)
{
	int channel;

	if (lda[tid].NumChannels > CHANNEL_MAX) lda[tid].NumChannels = CHANNEL_MAX;		// just in case...

	if (lda[tid].NumChannels == 1) return 0;                 			// single channel devices only have channel 0.

	if (lda[tid].NumChannels > 8) {

		// if we have a logical bank number we use it. The caller will zero this argument when the command format does not allow for a logical bank number
		// in a response
		if (bank != 0) {
			channel = ((bank-1) * 8) + BitMaskToChannel(tid, 8, mask);	// combine the logical bank value with the channel encoded in the mask bits
		}
		else {
			// we don't have an explicit logical bank, so we will infer the channel as best we can
			if (lda[tid].Channel > 7) {
				channel = lda[tid].Channel;				// we have to rely on the current channel setting since the report does not have a full channel value
			}
			else {
				channel = BitMaskToChannel(tid, 8, mask);		// our channel is encoded in the mask, it is on the master
			}
		}
  }
	else {
		if (lda[tid].NumChannels == 4) {
			channel = BitMaskToChannel(tid, 4, mask);				// for 4 channel devices the active channel is encoded in the mask
		}
		else {
			channel = BitMaskToChannel(tid, 8, mask);				// for 8 channel devices the active channel is encoded in the mask
		}
	}

	return channel;
}

void decode_status(unsigned char swpdata, int tid, int ch) {

	lda[tid].DevStatus[ch] &= ~(SWP_ACTIVE | SWP_REPEAT | SWP_BIDIRECTIONAL | PROFILE_ACTIVE);	// preset all the zero bit cases

	// are we ramping?
	if (swpdata & (SWP_ONCE | SWP_CONTINUOUS))
		lda[tid].DevStatus[ch] = lda[tid].DevStatus[ch] | SWP_ACTIVE;

     // -- fill in the SWP_UP status bit
	if (swpdata & (SWP_DIRECTION))  // are we ramping downwards?
		lda[tid].DevStatus[ch] = lda[tid].DevStatus[ch] & ~SWP_UP;
	else
		lda[tid].DevStatus[ch] = lda[tid].DevStatus[ch] | SWP_UP;

	// -- fill in the continuous sweep bit
	if (swpdata & (SWP_CONTINUOUS))  // are we in continuous ramp mode?
		lda[tid].DevStatus[ch] = lda[tid].DevStatus[ch] | SWP_REPEAT;

	// -- fill in the bidirectional ramp bit
	if (swpdata & (SWP_BIDIR))  // are we in bi-directional ramp mode?
		lda[tid].DevStatus[ch] = lda[tid].DevStatus[ch] | SWP_BIDIRECTIONAL;

	// -- fill in the profile active bit
	if (swpdata & (STATUS_PROFILE_ACTIVE))  // is an attenuation profile playing?
		lda[tid].DevStatus[ch] = lda[tid].DevStatus[ch] | PROFILE_ACTIVE;

}

// -- RD modified to ensure command responses from multi-channel devices are stored in the correct channel
//	  support added for profile command related response reports
//
void report_data_decode(unsigned char rcvdata[], int tid) {
  int ch = 0;			// channel index, set to our single channel default
  int tmp;				// general purpose temp
  int i;
  unsigned short dataval_16;
  unsigned int dataval_32;
  char temp[32];


  if ((DEBUG_OUT > 2) && (rcvdata[0] != 0x4e)) {
    printf("Decoding ");
    for (i=0; i<8; i++)
      printf("%02x ", rcvdata[i]);
    printf(" active channel = %d\r\n", 1+lda[tid].Channel);  // The API channel is 1-org and lda[].Channel is 0-org
  }

  // pre-compute some common payload values for command responses
  dataval_16 = rcvdata[2] + (rcvdata[3]<<8);
  dataval_32 = rcvdata[2] + (rcvdata[3]<<8) + (rcvdata[4]<<16) + (rcvdata[5]<<24);

  if ((DEBUG_OUT > 2) && !((rcvdata[0] == VNX_STATUS) || (rcvdata[0] == VNX_HRSTATUS) || (rcvdata[0] == VNX_HR8STATUS)))
	  printf("Two byte data payload decodes to %d, four byte data payload to %d\r\n", dataval_16, dataval_32);


  // handle the status reports and responses from the device
  // multi-channel devices all include a channel mask with their response
  // for single channel devices we just use channel 0.


  switch(rcvdata[0]) {
  // handle the status report for a traditional single channel attenuator
  case VNX_STATUS:
    if (DEBUG_OUT > 2) printf("VNX_STATUS: decoding\r\n");
    if (DevNotLocked(tid)) {

	  lda[tid].Attenuation[ch] = lda[tid].UnitScale * (int)rcvdata[7];	// update the attenuation level, converting HW units to our .05db units

      if (DEBUG_OUT > 2) printf("  VNX_STATUS reports Attenuation=%d\r\n", lda[tid].Attenuation[ch]);

	  // -- extract and save the status bits
	  decode_status(rcvdata[6], tid, ch);

      // -- fill in the current profile index --
	  lda[tid].ProfileIndex[ch] = rcvdata[4];
	  if (lda[tid].ProfileIndex[ch] > 99) lda[tid].ProfileIndex[ch] = 99;	// clip to profile length

      if (DEBUG_OUT > 2) printf("  VNX_STATUS sez Status=%02x\r\n", lda[tid].DevStatus[ch]);
      break;
    } /* if devnotlocked() */
    break;

  // handle the status report from a hi-res (and possibly 4 channel) attenuator
	// RD 9/25/20 modified to handle the status report for a 4 channel attenuator that is an expansion module
  case VNX_HRSTATUS:
    if (DEBUG_OUT > 2) printf("VNX_HRSTATUS: decoding\r\n");

    if (DevNotLocked(tid)) {
      if (lda[tid].NumChannels == 4 || lda[tid].Expandable) {
	    	// RD we don't want to change our global channel or channel mask based on a status report
	    	// but we need a temporary copy of the channel the HW is reporting
	    	// the channel mask bits are in the MSnibble of byteblock[1] instead of the LSnibble
	    	ch = BitMaskToChannel(tid, 4, (rcvdata[3] & 0xF0) >> 4);
				if (lda[tid].Expandable && rcvdata[1] != 0) {
					// RD we have a non-zero logical bank number for the channel, so adjust our channel accordingly
					ch = ch + 8*(rcvdata[1] & 0x07);
				}
      }

			// defend against a bad channel number
			if (ch >= CHANNEL_MAX) ch = CHANNEL_MAX -1;

      lda[tid].FrameNumber = (rcvdata[2] + (rcvdata[3]<<8)) & 0x000007FF;       // USB Frame Counter values are 11 bits

      lda[tid].Attenuation[ch] = (int)rcvdata[5] + 256 * (int)rcvdata[6];	// update the HiRes attenuation level (in .05 db units already)

      if (DEBUG_OUT > 2) printf("  VNX_HRSTATUS reports Attenuation=%d for channel %d\r\n", lda[tid].Attenuation[ch], ch+1);

	  	// -- extract and save the status bits
	  	decode_status(rcvdata[6], tid, ch);

      // -- fill in the current profile index --
			tmp = rcvdata[1];
			tmp = (tmp << 4) & 0x00000300;	// clear out all other bits but b9 and b8
      lda[tid].ProfileIndex[ch] = tmp | rcvdata[4];
      if (lda[tid].ProfileIndex[ch] > lda[tid].ProfileMaxLength - 1) lda[tid].ProfileIndex[ch] = lda[tid].ProfileMaxLength - 1;	// clip to max profile length for this device

      if (DEBUG_OUT > 2) printf("  VNX_HRSTATUS sez Status=%02x\r\n", lda[tid].DevStatus[ch]);
      break;
    } /* if devnotlocked() */
    break;

// handle the status report from a hi-res 8 channel attenuator and 8 channel expandable attenuator modules
  case VNX_HR8STATUS:
    if (DEBUG_OUT > 2) printf("VNX_HR8STATUS: decoding\r\n");

    if (DevNotLocked(tid)) {
      if (lda[tid].NumChannels >= 8 || lda[tid].Expandable) {
	    	// RD we don't want to change our global channel or channel mask based on a status report
	    	//	  but we need a temporary copy of the channel the HW is reporting
	    	//    the channel mask bits are in the MSnibble of byteblock[1] instead of the LSnibble
				if (rcvdata[3] & 0x08){
					ch = BitMaskToChannel(tid, 8, (rcvdata[3] & 0xF0));	      // this status mask is for channels 4 to 7
				}
				else{
					ch = BitMaskToChannel(tid, 8, (rcvdata[3] & 0xF0) >> 4);  // this status mask is for channels 0 to 3
				}

				// for an expandable LDA device we may have a status report from an expansion module
				if (lda[tid].Expandable && rcvdata[1] != 0) {
					ch = ch + 8*(rcvdata[1] & 0x07);
				}
      }

			// defend against a bad channel number
			if (ch >= CHANNEL_MAX) ch = CHANNEL_MAX -1;

      lda[tid].FrameNumber = (rcvdata[2] + (rcvdata[3]<<8)) & 0x000007FF; // USB Frame Counter values are 11 bits

      lda[tid].Attenuation[ch] = (int)rcvdata[5] + 256 * (int)rcvdata[6];	// update the HiRes attenuation level (in .05 db units already)

      if (DEBUG_OUT > 2) printf("  VNX_HR8STATUS reports Attenuation=%d\r\n", lda[tid].Attenuation[ch]);

	  	// -- extract and save the status bits
	  	decode_status(rcvdata[7], tid, ch);

      // -- fill in the current profile index --
			tmp = rcvdata[1];
			tmp = (tmp << 4) & 0x00000300;	// clear out all other bits but b9 and b8
      lda[tid].ProfileIndex[ch] = tmp | rcvdata[4];
      if (lda[tid].ProfileIndex[ch] > lda[tid].ProfileMaxLength - 1) lda[tid].ProfileIndex[ch] = lda[tid].ProfileMaxLength - 1;	// clip to max profile length for this device

      if (DEBUG_OUT > 2) printf("  VNX_HR8STATUS sez Channel %d Status=%02x\r\n", ch+1, lda[tid].DevStatus[ch]);
      break;
    } // if devnotlocked()
    break;

  // -- Decode the command responses --

  case VNX_FREQUENCY:
    if (DEBUG_OUT > 0) printf(" Working Frequency = %d\n", dataval_32);
    if (DevNotLocked(tid)){
      if(lda[tid].NumChannels > 1) {
		ch = MaskToChannel(tid, rcvdata[6], rcvdata[7]);		// responses from multi-channel devices include their channel ID, ch defaults to 0 for single channel devs
	  }
	  lda[tid].WorkingFrequency[ch] = dataval_32;
     }
    break;

  case VNX_PWR:
    if (lda[tid].DevStatus[0] & DEV_HIRES) {
      dataval_16 = (int)rcvdata[2] + 256 * (int)rcvdata[3];	// HiRes data is already in .05db units
    }
    else {
      dataval_16 = lda[tid].UnitScale * (int)rcvdata[2];	// We have to scale the other devices HW units to our .05db units
    }
    if (DEBUG_OUT > 0) printf(" Attenuation Level = %d\n", dataval_16);
    if (DevNotLocked(tid)) {
		if(lda[tid].NumChannels > 1) {
		  ch = MaskToChannel(tid, rcvdata[6], rcvdata[7]);	// responses from mc devs include the channel ID, ch defaults to 0 for single channel devs
	    }
	  lda[tid].Attenuation[ch] = dataval_16;
	}
    break;

  case VNX_ADWELL:
    if (DEBUG_OUT > 0) printf(" Dwell Time = %d\n", dataval_32);
    if (DevNotLocked(tid)) {
		if(lda[tid].NumChannels > 1) {
		  ch = MaskToChannel(tid, rcvdata[6], rcvdata[7]);	// responses from mc devs include the channel ID, ch defaults to 0 for single channel devs
	    }
      lda[tid].DwellTime[ch] = dataval_32;
	}
    break;

  case VNX_ADWELL2:
    if (DEBUG_OUT > 0) printf(" 2nd Dwell Time = %d\n", dataval_32);
    if (DevNotLocked(tid)){
		if(lda[tid].NumChannels > 1) {
		  ch = MaskToChannel(tid, rcvdata[6], rcvdata[7]);	// responses from mc devs include the channel ID, ch defaults to 0 for single channel devs
	    }
      lda[tid].DwellTime2[ch] = dataval_32;
	}
    break;

  case VNX_AIDLE:
    if (DEBUG_OUT > 0) printf(" Idle Time = %d\n", dataval_32);
    if (DevNotLocked(tid)) {
		if(lda[tid].NumChannels > 1) {
		  ch = MaskToChannel(tid, rcvdata[6], rcvdata[7]);	// responses from mc devs include the channel ID, ch defaults to 0 for single channel devs
	    }
      lda[tid].IdleTime[ch] = dataval_32;
		}
    break;

  case VNX_AHOLD:
    if (DEBUG_OUT > 0) printf(" Hold Time = %d\n", dataval_32);
    if (DevNotLocked(tid)) {
		if(lda[tid].NumChannels > 1) {
		  ch = MaskToChannel(tid, rcvdata[6], rcvdata[7]);	// responses from mc devs include the channel ID, ch defaults to 0 for single channel devs
	    }
      lda[tid].HoldTime[ch] = dataval_32;
		}
    break;

  case VNX_ASTART:
    if (lda[tid].DevStatus[0] & DEV_HIRES) {
      dataval_16 = (int)rcvdata[2] + 256 * (int)rcvdata[3];	// HiRes data is already in .05db units
    }
    else {
      dataval_16 = lda[tid].UnitScale * (int)rcvdata[2];	// We have to scale the other devices HW units to our .05db units
    }
    if (DEBUG_OUT > 0) printf(" Ramp Start Attenuation Level = %d\n", dataval_16);
    if (DevNotLocked(tid)) {
		if(lda[tid].NumChannels > 1) {
		  ch = MaskToChannel(tid, rcvdata[6], rcvdata[7]);	// responses from mc devs include the channel ID, ch defaults to 0 for single channel devs
	    }
      lda[tid].RampStart[ch] = dataval_16;
		}
    break;

  case VNX_ASTEP:
    if (lda[tid].DevStatus[0] & DEV_HIRES) {
      dataval_16 = (int)rcvdata[2] + 256 * (int)rcvdata[3];	// HiRes data is already in .05db units
    }
    else {
      dataval_16 = lda[tid].UnitScale * (int)rcvdata[2];	// We have to scale the other devices HW units to our .05db units
    }
    if (DEBUG_OUT > 0) printf(" Attenuation Step Size = %d\n", dataval_16);
    if (DevNotLocked(tid)) {
		if(lda[tid].NumChannels > 1) {
		  ch = MaskToChannel(tid, rcvdata[6], rcvdata[7]);	// responses from mc devs include the channel ID, ch defaults to 0 for single channel devs
	    }
      lda[tid].AttenuationStep[ch] = dataval_16;
		}
    break;

  case VNX_ASTEP2:
    if (lda[tid].DevStatus[0] & DEV_HIRES) {
      dataval_16 = (int)rcvdata[2] + 256 * (int)rcvdata[3];	// HiRes data is already in .05db units
    }
    else {
      dataval_16 = lda[tid].UnitScale * (int)rcvdata[2];	// We have to scale the device's HW units to our .05db units
    }
    if (DEBUG_OUT > 0) printf(" Attenuation Step Size Two = %d\n", dataval_16);
    if (DevNotLocked(tid)) {
		if(lda[tid].NumChannels > 1) {
		  ch = MaskToChannel(tid, rcvdata[6], rcvdata[7]);	// responses from mc devs include the channel ID, ch defaults to 0 for single channel devs
	    }
      lda[tid].AttenuationStep2[ch] = dataval_16;
		}
    break;

  case VNX_ASTOP:
    if (lda[tid].DevStatus[0] & DEV_HIRES) {
      dataval_16 = (int)rcvdata[2] + 256 * (int)rcvdata[3];	// HiRes data is already in .05db units
    }
    else {
      dataval_16 = lda[tid].UnitScale * (int)rcvdata[2];	// We have to scale the device's HW units to our .05db units
    }
    if (DEBUG_OUT > 0) printf(" Ramp End Attenuation Level = %d\n", dataval_16);
    if (DevNotLocked(tid)) {
		if(lda[tid].NumChannels > 1) {
		  ch = MaskToChannel(tid, rcvdata[6], rcvdata[7]);	// responses from mc devs include the channel ID, ch defaults to 0 for single channel devs
	    }
      lda[tid].RampStop[ch] = dataval_16;
		}
    break;

  case VNX_SWEEP:
    if (DEBUG_OUT > 0) printf(" Ramp Mode = %d\n", rcvdata[2]);
    if (DevNotLocked(tid)) {
 			// not generally used, same information available in the VNX_STATUS reports (and would be overwritten by the next status report...)
	  	if(lda[tid].NumChannels > 1) {
		  ch = MaskToChannel(tid, rcvdata[6], rcvdata[7]);	// responses from mc devs include the channel ID, ch defaults to 0 for single channel devs
	  }
	  	// -- extract and save the status bits
	  	decode_status(rcvdata[2], tid, ch);
    }
    break;

  case VNX_RFMUTE:
    if (DEBUG_OUT > 0) printf("Parsing a RFMUTE report..\n");
    if (DEBUG_OUT > 0) {
      if (rcvdata[2])
				strcpy(temp, "RF ON");
      else
				strcpy(temp, "RF OFF");

      printf("%s \n", temp);
    }

    if (DevNotLocked(tid)) {
		if(lda[tid].NumChannels > 1) {
		  ch = MaskToChannel(tid, rcvdata[6], rcvdata[7]);	// responses from mc devs include the channel ID, ch defaults to 0 for single channel devs
	  }
      if (rcvdata[0])
				lda[tid].Modebits[ch] = lda[tid].Modebits[ch] | MODE_RFON;
      else
				lda[tid].Modebits[ch] = lda[tid].Modebits[ch] & ~MODE_RFON;
    }
    break;

	// --- profile related responses ---
	case VNX_PROFILEDWELL:
	    if (DEBUG_OUT > 0) printf(" Profile Dwell Time = %d\n", dataval_32);
    if (DevNotLocked(tid)){
		if(lda[tid].NumChannels > 1) {
		  ch = MaskToChannel(tid, rcvdata[6], rcvdata[7]);	// responses from mc devs include the channel ID, ch defaults to 0 for single channel devs
	    }
      lda[tid].ProfileDwellTime[ch] = dataval_32;
	}
    break;

	case VNX_PROFILEIDLE:
	    if (DEBUG_OUT > 0) printf(" Profile Idle Time = %d\n", dataval_32);
    if (DevNotLocked(tid)){
		if(lda[tid].NumChannels > 1) {
		  ch = MaskToChannel(tid, rcvdata[6], rcvdata[7]);	// responses from mc devs include the channel ID, ch defaults to 0 for single channel devs
	    }
      lda[tid].ProfileIdleTime[ch] = dataval_32;
	}
    break;

	case VNX_PROFILECOUNT:
	    if (DEBUG_OUT > 0) printf(" Profile Count = %d\n", dataval_32);
    if (DevNotLocked(tid)){
		if(lda[tid].NumChannels > 1) {
		  ch = MaskToChannel(tid, rcvdata[6], rcvdata[7]);	// responses from mc devs include the channel ID, ch defaults to 0 for single channel devs
	    }
      lda[tid].ProfileCount[ch] = dataval_32;
	}
    break;

  case VNX_SETPROFILE:
	if (DevNotLocked(tid)){
		if(lda[tid].NumChannels > 1) {
		  ch = MaskToChannel(tid, 0, rcvdata[7]);		// responses from mc devs include the channel ID, ch defaults to 0 for single channel devs
																								// profile element responses do not include a bank ID
	    }

		if (lda[tid].DevStatus[0] & DEV_HIRES) {
			i = rcvdata[5] & 0x03;			// devices with long profiles return the two high bits of the index in rcvdata[5]
			i = (i << 8) | rcvdata[6];		// HiRes devices report index in report[6], and the value in report[2]:report[3], max profile length = 50
			if ( i >= 0 && i < PROFILE_MAX_RAM){
				lda[tid].CachedProfileValue[ch] = ((i << 16) | (((int) dataval_16) & 0xFFFF));
			}
		}
		else {
			i = rcvdata[4];	// standard legacy LDA devices report the index in report[4], and the value in report[5], max profile length = 100

			if ( i >= 0 && i < PROFILE_MAX){
				lda[tid].CachedProfileValue[ch] = ((i << 16) | ((lda[tid].UnitScale * (int)rcvdata[5]) & 0xFFFF));		// RD this was a bug, reading rcvdata[6] instead of rcvdata[5]. Fixed 8/2020.
			}
		}

		if (DEBUG_OUT > 2) printf(" profile element cache = %x\n", lda[tid].CachedProfileValue[ch]);
		if (DEBUG_OUT > 1) printf(" profile element %d = %d\n", i, lda[tid].CachedProfileValue[ch] & 0xFFFF);
	}
	break;

  case VNX_CHANNEL:
	if (DevNotLocked(tid)){

		if (rcvdata[3] > 0) {
			lda[tid].GlobalChannel = (int) (rcvdata[3] - 1);	// our host side GlobalChannel is zero based, the packet uses a 1 based format

			if (lda[tid].Expandable == false) {
				lda[tid].Expandable = true;
			}

			lda[tid].Channel = lda[tid].GlobalChannel;
			if (lda[tid].Channel < 8) {
				lda[tid].ChMask = 0x01 << lda[tid].Channel;		// RD -- future proofing, the else case could handle all values of channel for now
			}
			else {
				lda[tid].ChMask = 0x01 << (lda[tid].Channel % 8);
			}
		}
		else {
			lda[tid].GlobalChannel = 0;		// this should not happen, but if we got a bad USB read it might
			break;												// bail out...
		}

		if (lda[tid].GlobalChannel > (CHANNEL_MAX-1)) {
			lda[tid].GlobalChannel = CHANNEL_MAX - 1;			// this should not happen, but we defend our arrays anyway
		}

		if(lda[tid].NumChannels > 1) {
		  ch = MaskToChannel(tid, 0, rcvdata[7]);	// responses from mc devs include the channel ID, ch defaults to 0 for single channel devs
	    }

		// get the number of expansion channels to find the total number of channels we have
		if (lda[tid].Expandable) {
			lda[tid].NumChannels = rcvdata[3] + lda[tid].BaseChannels;
		}

		if (DEBUG_OUT > 1) printf(" Global Channel %d of %d total channels\n", lda[tid].GlobalChannel, lda[tid].NumChannels);
		}

	break;

	// --- these are global parameters for a device, so not saved by channel ---
  case VNX_MINATTEN:
    if (lda[tid].DevStatus[0] & DEV_HIRES) {
      dataval_16 = (int)rcvdata[2] + 256 * (int)rcvdata[3];	// HiRes data is already in .05db units
    }
    else {
      dataval_16 = lda[tid].UnitScale * (int)rcvdata[2];		// We have to scale the other devices HW units to our .05db units
    }
    if (DEBUG_OUT > 0) printf(" Minimum Attenuation = %d\n", dataval_16);
    if (DevNotLocked(tid))
      lda[tid].MinAttenuation = dataval_16;
    break;

  case VNX_MAXATTEN:
    if (lda[tid].DevStatus[0] & DEV_HIRES) {
      dataval_16 = (int)rcvdata[2] + 256 * (int)rcvdata[3];	// HiRes data is already in .05db units
    }
    else {
      dataval_16 = lda[tid].UnitScale * (int)rcvdata[2];		// We have to scale the other devices HW units to our .05db units
    }
    if (DEBUG_OUT > 0) printf(" Maximum Attenuation = %d\n", dataval_16);
    if (DevNotLocked(tid))
      lda[tid].MaxAttenuation = dataval_16;
    break;

  case VNX_GETSERNUM:
    if (DEBUG_OUT > 0) printf(" Serial Number = %d\n", dataval_32);
    if (DevNotLocked(tid))
      lda[tid].SerialNumber = dataval_32;		// NB -- we only use this path during enumeration
    break;
  } /* switch */

  return;
}


// ************* The read thread handler for the brick ***********************
//
// RD 4-2019 modified to move open/close interaction with libusb to the
// Note -- 	the thread_command is used both for commands and thread status reporting
//			thread code must ensure that it has read the command before over-writing it, and must not overwrite a thread status
//
void *brick_handler_function (void *threadID) {
  int i, tid;
  tid = (int)(uintptr_t)threadID;
  int usb_status;
  char fullpath[128];
  int retries;
  int cnt, cntidx;
  int xfer_length;
  libusb_device **devlist, *dev ;
  libusb_device_handle *devhandle = NULL;

  if (DEBUG_OUT > 0) printf("Starting thread for device %d\r\n", tid);

  // -- top level thread loop --
  while ((lda[tid].thread_command >=0) && (lda[tid].thread_command != THREAD_EXIT)) {
    switch(lda[tid].thread_command) {
    case THREAD_IDLE:
      // this is where we launch a read and wait for incoming USB data
      usb_status = -1;
      retries = 10;
      while ((usb_status < 0) && (retries--) && (lda[tid].thread_command == THREAD_IDLE)) {

				if (DEBUG_OUT > 2) printf("Calling libusb_interrupt_transfer with DevHandle = %p, Endpoint = %x, rcvbuff = %p\r\n",(void *)lda[tid].DevHandle, lda[tid].Endpoint, (void *)lda[tid].rcvbuff);

				usb_status = libusb_interrupt_transfer(lda[tid].DevHandle,  // device handle
					lda[tid].Endpoint,	// endpoint
					lda[tid].rcvbuff,		// buffer
					PACKET_INT_LEN,			// max length
					&xfer_length,				// could be actual transfer length
					TIMEOUT);

				if (DEBUG_OUT > 2) printf("libusb_interrupt_transfer returned %x\r\n", usb_status);

				if (usb_status == LIBUSB_ERROR_NO_DEVICE) {
					// our device was unplugged, so we should clean up and end this thread
					lda[tid].DevStatus[0] = lda[tid].DevStatus[0] & ~DEV_CONNECTED;
					lda[tid].thread_command = THREAD_EXIT;
				}

				if (usb_status < 0 && (usb_status != LIBUSB_ERROR_TIMEOUT)) catnap(2); /* wait 2 ms before trying again for most errors */
      }

		  // did we get some data?
      if ((usb_status == 0) && (xfer_length > 0)) {
				if (DEBUG_OUT > 2) {
					printf("Thread %d reports %d...", tid, usb_status);
					for (i=0; i<usb_status; i++)
						printf("%02x ", lda[tid].rcvbuff[i]);
					printf("\r\n");
				}
				/* decode the HID data */
				report_data_decode(lda[tid].rcvbuff, tid);
				if (DEBUG_OUT > 2) printf("Decoded device %d data %02x, decodewatch=%02x\r\n", tid, lda[tid].rcvbuff[0], lda[tid].decodewatch);
				if (lda[tid].decodewatch == lda[tid].rcvbuff[0]) {
					if (DEBUG_OUT > 2) printf("Clearing decodewatch %02x for thread %d\r\n", lda[tid].decodewatch, tid);
					lda[tid].decodewatch = 0;
				}
      } else
				if (DEBUG_OUT > 0) perror("THREAD_IDLE");

      break;


    case THREAD_START:
		// this is the first code that the thread will run when started
		// since the device open was handled by our API level code we don't have much to do here


		// transition to the read state
		lda[tid].thread_command = THREAD_IDLE;
	  break;

    } /* switch */
  } /* while */

  // -- we received a THREAD_EXIT command
  if (DEBUG_OUT > 0) printf("Exiting thread for device %d because command=%d\r\n", tid, lda[tid].thread_command);
  lda[tid].thread_command = THREAD_DEAD;
  pthread_exit(NULL);
}

// -------------- SendReport -------------------------------------------------

bool SendReport(int deviceID, char command, char *pBuffer, int cbBuffer)
{
  int i;
  int send_status;
  int retries;
	int lastSendSeconds;
	long lastSendNanoSeconds;

  // Make sure the buffer that is being passed to us fits
  if (cbBuffer > HR_BLOCKSIZE) {
    // Report too big, don't send!
    return FALSE;
  }

  // lets make sure our command doesn't have any junk bits in it
  for (i=0; i<HID_REPORT_LENGTH; i++)
	  lda[deviceID].sndbuff[i] = 0;

  if (DEBUG_OUT > 1) printf("SR: command=%x cbBuffer=%d\r\n", (uint8_t)command, cbBuffer);
  lda[deviceID].sndbuff[0] = command;		// command to device
  lda[deviceID].sndbuff[1] = cbBuffer;
  for (i=0; i<cbBuffer; i++)
    lda[deviceID].sndbuff[i+2] = pBuffer[i];

  if (DEBUG_OUT > 1) {
    printf("SR to device %d: ", deviceID);
    for (i=0; i<8; i++) {
      printf("%02x ", (uint8_t)lda[deviceID].sndbuff[i]);
    }
    printf("\r\n");
  }

  // we wait for a file handle to appear in case we get here before the device really opened (unlikely...)
  retries = 0;
  while ((0 == lda[deviceID].DevHandle) && (retries++ < 10))
    sleep(1);

	// we may have to wait in order to ensure that very fast USB host controllers don't overrun our device
	if (HasHRTimer){
		clock_gettime(CLOCK_MONOTONIC_COARSE, &mtimer_CTime);
		lastSendSeconds = mtimer_CTime.tv_sec - mtimer_LastSend.tv_sec;
		lastSendNanoSeconds = mtimer_CTime.tv_nsec - mtimer_LastSend.tv_nsec;

		if (DEBUG_OUT > 2) printf("SR: last send was %d seconds and %ld nanoseconds ago\r\n", lastSendSeconds, lastSendNanoSeconds);

		if (lastSendSeconds == 1){
			// we may have rolled over, so check if our last time was at least 1 ms earlier
			lastSendNanoSeconds = mtimer_CTime.tv_nsec + (1000000000L - mtimer_LastSend.tv_nsec);
			if (lastSendNanoSeconds < 2000000L){																					// RD Testing, was 1000000L for 1ms minimum time
				// we want to wait enough to allow 2ms between USB Commands
				mtimer_CTime.tv_sec = 0;
				mtimer_CTime.tv_nsec = 2000000L - lastSendNanoSeconds;
				if (DEBUG_OUT > 2) printf("SR: rollover delay = %ld\r\n", mtimer_CTime.tv_nsec);
				nanosleep(&mtimer_CTime , NULL);
			}
		}
		else if (lastSendSeconds == 0 && lastSendNanoSeconds < 2000000L) {							// RD Testing, was 1000000L for 1ms minimum time
				// we want to wait enough to allow 2ms between USB Commands
				mtimer_CTime.tv_sec = 0;
				mtimer_CTime.tv_nsec = 2000000L - lastSendNanoSeconds;
				if (DEBUG_OUT > 2) printf("SR: delay = %ld\r\n", mtimer_CTime.tv_nsec);
				nanosleep(&mtimer_CTime , NULL);
		}
		else {
			// if lastSendSeconds > 1 or the nanoseconds portion is > 1 ms we don't have to worry about it...
			if (DEBUG_OUT > 2) printf("SR: no delay needed, last send was %d seconds and %ld nanoseconds ago\r\n", lastSendSeconds, lastSendNanoSeconds);
		}
	}
	else {
		// we don't have a timer, so we'll just wait 1ms
		 mtimer_CTime.tv_sec = 0;
		 mtimer_CTime.tv_nsec = 1000000L;
		 if (DEBUG_OUT > 2) printf("SR: no HR time fixed delay = %ld\r\n", mtimer_CTime.tv_nsec);
		 nanosleep(&mtimer_CTime , NULL);
	}

	// update the send time
	clock_gettime(CLOCK_MONOTONIC_COARSE, &mtimer_LastSend);

  /* we have data to write to the device */
  if (DEBUG_OUT > 1) printf("SR: sending the write to handle %p...\r\n", (void *)lda[deviceID].DevHandle);
  send_status = libusb_control_transfer(lda[deviceID].DevHandle,
				0x21,
				0x09, //HID_REPORT_SET,
				0x200,
				0,
				lda[deviceID].sndbuff,
				PACKET_CTRL_LEN,
				TIMEOUT);
  if (DEBUG_OUT > 1) printf("SR: sent it...\r\n");
  if (DEBUG_OUT > 1) {
    printf("(status=%d handle=%p)", send_status, (void *)lda[deviceID].DevHandle);
    if (send_status < 0) perror("SendReport"); else printf("\r\n");
  }
  return TRUE;
}

// ------------ GetParameter ---------------------------------------------
//
// The GetParam argument is the command byte sent to the device to get
// a particular value. The response is picked up by the read thread and
// parsed by it. The parser clears the corresponding event.


bool GetParameter(int deviceID, int GetParam)
{
	char VNX_param[6] = {0, 0, 0, 0, 0, 0};
	int timedout;

	if (DEBUG_OUT > 0) printf(" sending a GET command = %x\n", (char) GetParam );
	lda[deviceID].decodewatch = (char) GetParam;
	if (!SendReport(deviceID, (char)GetParam, VNX_param, 0)) {
	  return FALSE;
	}

	if (DEBUG_OUT > 0) printf(" SendReport sent a GET command successfully to device %d = %x\n", deviceID, (char) GetParam );

	starttime = time(NULL);
	timedout = 0;

	// wait until the value is decoded or 2 seconds have gone by
	// RD 4-2019 modified to yield the thread during the wait
	while ((lda[deviceID].decodewatch > 0) && (0 == timedout)) {

	  catnap(10);	// yield for 10 ms
	  if ((time(NULL)-starttime) > 2) timedout = 1;
	}

	return (0 == timedout) ? TRUE : FALSE;
}

// -------------- Get Routines to read device settings --------------------
//
// Note: for these functions deviceID is not checked for validity
//		 since it was already checked in the calling program.

bool GetAttenuation(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_PWR))
    return FALSE;

  return TRUE;
}

bool GetFrequency(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_FREQUENCY))
    return FALSE;

  return TRUE;
}

// -------------------------------

bool GetIdleTime(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_AIDLE))
    return FALSE;

  return TRUE;
}

// -------------------------------

bool GetHoldTime(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_AHOLD))
    return FALSE;

  return TRUE;
}
// -------------------------------

bool GetRampStart(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_ASTART))
    return FALSE;

  return TRUE;
}

// -------------------------------
bool GetRampStep(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_ASTEP))
    return FALSE;

  return TRUE;
}

// -------------------------------
bool GetRampStep2(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_ASTEP2))
    return FALSE;

  return TRUE;
}
// -------------------------------

bool GetRampEnd(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_ASTOP))
    return FALSE;

  return TRUE;
}

// -------------------------------
bool GetDwellTime(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_ADWELL))
    return FALSE;

  return TRUE;
}

// -------------------------------
bool GetDwellTime2(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_ADWELL2))
    return FALSE;

  return TRUE;
}

// -------------------------------
bool GetRF_On(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_RFMUTE))
    return FALSE;

  return TRUE;
}

// -------------------------------
bool GetWorkingFrequency(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_FREQUENCY))
    return FALSE;

  return TRUE;
}

// -------------------------------
bool GetRampStop(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_ASTOP))
    return FALSE;

  return TRUE;
}

// -------------------------------
bool GetAttenuationStep(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_ASTEP))
    return FALSE;

  return TRUE;
}

// -------------------------------
bool GetAttenuationStep2(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_ASTEP2))
    return FALSE;

  return TRUE;
}

// ---- profile related Get functions ------
bool GetProfileCount(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_PROFILECOUNT))
    return FALSE;

  return TRUE;
}

bool GetProfileDwellTime(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_PROFILEDWELL))
    return FALSE;

  return TRUE;
}

bool GetProfileIdleTime(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_PROFILEIDLE))
    return FALSE;

  return TRUE;
}

bool GetChannel(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_CHANNEL))
    return FALSE;

  return TRUE;
}


// RD -- Revised to use a single element cache, see the response parser for details
bool GetProfileElement(DEVID deviceID, int index)
{
    int max_index;
		int timedout;

		max_index = lda[deviceID].ProfileMaxLength - 1;

		if (max_index < 0 || max_index > PROFILE_MAX_RAM - 1) lda[deviceID].ProfileMaxLength = 50;	// this should never happen, if it does, pick the smallest profile size for safety

    if (index < 0 || index > max_index) return FALSE;    // bail out on a bad index...

    BYTE VNX_command[] = {0, 0, 0, 0, 0, 0};

    if (CheckHiRes(deviceID)){
				VNX_command[3] = (BYTE)((index >> 8) & 0x03);
        VNX_command[4] = (BYTE)index;        // HiRes LDA devices have the index in byteblock[4]
		}
    else{
        VNX_command[1] = (BYTE)index;        // standard V2 LDA devices have the index in byteblock[1]
		}
    // --- first we send the command out to the device --
		lda[deviceID].decodewatch = (char) VNX_SETPROFILE;
    if (!SendReport(deviceID, VNX_SETPROFILE | VNX_GET, VNX_command, 5))
        return FALSE;

    // --- then we wait for the read thread's parser to signal that it got the response
	starttime = time(NULL);
	timedout = 0;

	// wait until the value is decoded or 2 seconds have gone by
	// RD 4-2019 modified to yield the thread during the wait
	while ((lda[deviceID].decodewatch > 0) && (0 == timedout)) {
	  catnap(10);	// yield for 10 ms, speed matters here
	  if ((time(NULL)-starttime) > 2) timedout = 1;
	}
	return (0 == timedout) ? TRUE : FALSE;
}



bool GetSerNum(DEVID deviceID) {
  if (!GetParameter(deviceID, VNX_GETSERNUM))
    return FALSE;

  return TRUE;
}




/* functions to manage devices, not getting or retrieving data */
/*-------------------------------------------------------------*/

// function to get the serial number of the device, returns negative values for errors, or the actual serial number
// certain LDA-602Q devices return a different USB serial number string than their actual serial number
// This function returns the actual device serial number. While it is theoretically possible for a device to have a serial number of 0,
// this code assumes non-zero serial numbers.
//
// This function is called with an open devhandle and leaves it open for the caller to close
//
int GetSerialNumber(libusb_device_handle *devhandle, int HWType, uint8_t sernum_string_desc_index, char* str_sernum, int * features)
{
	int i;
	int usb_status;
	int serial_number = 0;
	char rcvbuff[32];
	int TDev = 0;
	int retries;

	// quick sanity check
	if (devhandle == NULL) return DEVICE_NOT_READY;		// somehow we don't have a valid device handle, so bail out now

	// protect our receive buffer
	for (i=0; i<32; i++) rcvbuff[i]=0;

	if (features != NULL)
		*features = DEFAULT_FEATURES;

	// get the USB serial number string
	usb_status = libusb_get_string_descriptor_ascii(devhandle, sernum_string_desc_index, rcvbuff, sizeof(rcvbuff)-1);

	if (DEBUG_OUT > 1) printf("SerNum string %d = [%s]\r\n", sernum_string_desc_index, rcvbuff);
	if (usb_status < 0) return BAD_HID_IO;

	// the USB serial number string starts with SN: so we skip those characters
	serial_number = atoi(rcvbuff+3);

	if (HWType == 11 && (serial_number >= 14463) && serial_number <= 14465)
	{
		// we need to ask the device for the actual device serial number

		// Initialize our temporary device data structure lda[0]
		lda[TDev].DevStatus[0] = DEV_CONNECTED;
		lda[TDev].DevType = 11;
		lda[TDev].Endpoint = ENDPOINT_INT_IN1;
		lda[TDev].DevHandle = devhandle;

		// set up the kernel driver and device configuration

		if (DEBUG_OUT > 1) printf("  Detaching kernel driver DevHandle = %p\r\n", (void *)lda[TDev].DevHandle);
		usb_status = libusb_detach_kernel_driver(lda[TDev].DevHandle, 0);

		if ((DEBUG_OUT > 2) && (usb_status != 0)) printf("  Detaching kernel driver returned error status = %x\r\n", usb_status);

		// In theory the libusb kernel driver might already be attached, so we ignore the LIBUSB_ERROR_NOT_FOUND case
		if ((usb_status != 0) && (usb_status != LIBUSB_ERROR_NOT_FOUND)) {

			// very unlikely, but we might have a disconnected device, so check for that
			if (usb_status == LIBUSB_ERROR_NO_DEVICE) {
				return DEVICE_NOT_READY;
			}
			else {
				return BAD_HID_IO;		// not necessarily IO layer, but something failed in the kernel stack below us
			}
		}

		if (DEBUG_OUT > 2) printf("  Setting configuration %04x:%04x\r\n", lda[TDev].idVendor, lda[TDev].idProduct);
		usb_status = libusb_set_configuration (lda[TDev].DevHandle, 1);

		if (DEBUG_OUT > 2) printf ("  successfully set configuration: %s\n", usb_status ? "failed" : "passed");

		if (usb_status != 0) {
			// unlikely, but we might have a disconnected device, so check for that
			if (usb_status == LIBUSB_ERROR_NO_DEVICE) {
					return DEVICE_NOT_READY;
				}
			else
				return BAD_HID_IO;		// not necessarily IO layer, but something failed in the kernel stack below us
		}

			usb_status = libusb_claim_interface (lda[TDev].DevHandle, 0);

			if (DEBUG_OUT > 1) printf ("claim interface: %s\n", usb_status ? "failed" : "passed");

			if (usb_status != 0) {
				return BAD_HID_IO;		// not necessarily IO layer, but something failed in the kernel stack below us
			}

		// Start up a read thread so we can get responses from the device
		// since the device is already opened we do a quick start of the read thread
		lda[TDev].thread_command=THREAD_START;
		pthread_create(&threads[0], NULL, brick_handler_function, (void*)(uintptr_t)TDev);

		// wait here for the thread to start up and get out of the startup state
		retries = 10;
		while (retries && (lda[TDev].thread_command == THREAD_START)) {
				catnap(100);  /* wait 100 ms */
				retries--;
			}

		if (lda[TDev].thread_command == THREAD_START) {

			// for some reason our thread failed to start, not much we can do but bail out
			lda[TDev].thread_command = THREAD_EXIT;

			// wait here for the thread to exit on its own
			retries = 10;
			while (retries && (lda[TDev].thread_command != THREAD_DEAD)) {
				catnap(100);  /* wait 10 ms */
				retries--;
			}

			// the thread didn't exit on its own, so just cancel it
			if (lda[TDev].thread_command != THREAD_DEAD) {
				pthread_cancel(threads[0]);
			}

			return BAD_HID_IO;

		}

		lda[TDev].DevStatus[0] = lda[TDev].DevStatus[0] | DEV_OPENED;	// Even for multichannel devices, we can use channel 0
																		// to know if the device is open
		if (DEBUG_OUT > 2) printf("reading internal serial number\r\n");
		if (!GetSerNum(TDev)) {
			return BAD_HID_IO;
		}

		if (DEBUG_OUT > 2) printf("internal serial number is %d\r\n", lda[TDev].SerialNumber);
		serial_number = lda[TDev].SerialNumber;

		// This is an LDA-602Q in LDA-102E emulation mode, it actually has 4 channels
		// NB -- this value will be used by our caller to tweak the HWType, etc.
		if (features != NULL)
			*features = HAS_4CHANNELS;

		// we need to shut down our read thread
		lda[TDev].thread_command = THREAD_EXIT;

		// The thread handler will clear any pending reads on the device. We'll wait up to 1 second then give up.
		retries = 10;
		while (retries && (lda[TDev].thread_command != THREAD_DEAD)) {
			catnap(100);  /* wait 100 ms */
			retries--;
		}

		// if for some reason the thread didn't exit on its own, just cancel it
		if (lda[TDev].thread_command != THREAD_DEAD) {
			pthread_cancel(threads[0]);
		}

		if (DEBUG_OUT > 2) printf("After telling the thread to close, we have thread_command=%d retries=%d\r\n", lda[TDev].thread_command, retries);

	    // Mark it closed in our list of devices
		lda[TDev].DevStatus[0] = lda[TDev].DevStatus[0] & ~DEV_OPENED;

		// -- we don't do a libusb close here since our caller does that --
		if (str_sernum != NULL)
			sprintf(str_sernum, "%d", serial_number);

		return serial_number;
	}
	else {
		// we can just use the USB serial number value
		if (str_sernum != NULL)
			strcpy(str_sernum, rcvbuff+3);

		return serial_number;
	}

}

// This function finds the LDA devices and sets up the parameters for each device it finds
//
//
void FindVNXDevices()
{
  bool bFound;
  int hTemp;  				// temporary variable
  int HWType; 				// temporary variable for hardware/model type
  int HWMinAttenuation;		// temporary variable for default minimum attenuation
  int HWMaxAttenuation;		// temporary variable for default maximum attenuation
  int HWAttenuationStep;	// temporary variable for the devices minimum attenuation step in .05 db units
  int HWMinFrequency;		// temporary variable for HRes minimum frequency
  int HWMaxFrequency;		// temporary variable for HRes maximum frequency
  int HWUnitScale;			// temporary variable for unit scaling -- we use .05db internally
  char HWName[MAX_MODELNAME];  		// temporary variable for the hardware model name
  char HWSerial[8]; 		// temporary holder for the serial number
  int HWSerialNumber;		// temporary holder for the device serial number
  int HWNumChannels;        // temporary holder for the number of channels
  bool HWExpandable;		// temporary holder for our flag to indicate a device which can have expansion channels
  int HWMaxProfileRAM;
  int HWMaxProfileSaved;
  bool HWHasHiRes;			// true for HiRes devices
  int HWFeatures;
  int HWBusAddress;			// temporary holder for the USB bus address of the device, used to find the device later
  int HWEndpoint;			// temporary holder for the HW endpoint used for status reports

  libusb_device_handle *devhandle = NULL;
  char sendbuff[8];
  char rcvbuff[32];
  int usb_status;

  int busnum;
  int busaddress;

  if (!Did_libusb_init){
	usb_status = libusb_init(&LDA_ctx);		// just in case...
	if (usb_status != 0) return;			// we can't really return an error, but hopefully we won't just crash
	Did_libusb_init = TRUE;
  }

#if FORCE_LUSB_DEBUG
  libusb_set_debug(NULL, 0); 	/* RD - if we want lots of debug, let's see the libusb output too. 0 for none, 4 for a lot */
#endif


#if 0
  // for libusb versions after 1.0.21 use set_option
  if (DEBUG_OUT > 2)
    libusb_set_option(NULL, LIBUSB_OPTION_LOG_LEVEL, 4); 	/* if we want lots of debug, let's see the USB output too. */
  else
    libusb_set_option(NULL, LIBUSB_OPTION_LOG_LEVEL, 0);
  //#else

  // for libusb versions before 1.0.22 use set_debug
  if (DEBUG_OUT > 2)
    libusb_set_debug(NULL, 4); 	/* if we want lots of debug, let's see the USB output too. */
  else
    libusb_set_debug(NULL, 0);
#endif

  libusb_device **devlist, *dev ;
  int r;
  ssize_t cnt;
  int c, i, a, j;
  int send_status, open_status;
  int cntidx;

  /* ... */

  // We need to remove devices from our table that are no longer connected ---
  // to do this we clear the "connected" flag for each table entry that is not open initially
  // then, as we find them we re-set the "connected" flag
  // anybody who doesn't have his "connected" flag set at the end is gone - we found it
  // previously but not this time

  for (i = 1; i<MAXDEVICES; i++){
    // even for multichannel devices we can look at channel 0 to see if it's open or connected
    if ((lda[i].SerialNumber != 0) && !(lda[i].DevStatus[0] & DEV_OPENED))
      lda[i].DevStatus[0] = lda[i].DevStatus[0] & ~DEV_CONNECTED;
  }

  // Grab the list of devices and see if we like any of them
  cnt = libusb_get_device_list(NULL, &devlist);
  for (cntidx=0; cntidx<cnt; cntidx++) {
    dev = devlist[cntidx];
    struct libusb_device_descriptor desc;

    usb_status = libusb_get_device_descriptor(dev, &desc);
    if (DEBUG_OUT > 1) printf("Vendor: %04x PID: %04x (%d of %ld)\r\n", desc.idVendor, desc.idProduct, cntidx, cnt);

    HWType = 0;
    HWMinAttenuation = 0;
    HWMinFrequency = 0;
    HWMaxFrequency = 0;					// defaults for non HiRes devices
	HWHasHiRes = FALSE;
	HWMaxProfileRAM = PROFILE_MAX;
	HWMaxProfileSaved = PROFILE_MAX;	// preset the common case
    HWNumChannels = 1;  				// default for everything except quad LDA, 906V-8, etc.
	HWExpandable = false;				// most hardware does not support adding expansion mobules
	HWEndpoint = ENDPOINT_INT_IN2;		// default for many LDA devices

	// check to see if the device is one of our devices
	if (desc.idVendor == devVID) {
		// look through our PIDs to see if any match the device
		for (i = 1; i<(sizeof LDA_Pids / sizeof LDA_Pids[0]); i++) {
			if (LDA_Pids[i] == desc.idProduct){
				HWType = i;
				break;
			}
		}
	}

    if (DEBUG_OUT > 2) printf("Found HWType = %d\r\n", HWType);

    if (HWType) { /* we like this device and we'll keep it */

	  // gather the USB address for this device so we can find it in the future
	  busnum = (int)libusb_get_bus_number(dev);
	  busaddress = (int)libusb_get_device_address(dev);
	  if (DEBUG_OUT > 2) printf("USB bus = %d, address =%d\r\n", busnum, busaddress);

	  HWBusAddress = (busnum << 8) | busaddress;

	  if (DEBUG_OUT > 1) printf("Opening LDA device %04x:%04x type %d at bus address %02x\r\n",
				desc.idVendor,
				desc.idProduct,
				HWType, HWBusAddress);

	  // lets open the device to gather more information about it
      usb_status = libusb_open(dev, &devhandle);
      if (usb_status < 0) {
			if (DEBUG_OUT > 0) 	printf("FindVNXDevices sez: I can't open the device!\r\n");
			// The most likely cause of the libusb_open failure is a device we already have open for an application -- so skip over it
			continue;
      }
	  else {
		// --- our device open succeeded, lets set the parameters for each type of hardware ---
		switch(HWType) {
			case 1: // LDA-102
					strcpy(HWName, sVNX1);
					HWMaxAttenuation = 1260;	// 63db max attenuation
					HWAttenuationStep = 10;		// .5db
					HWUnitScale = 5;			// .25db units
					break;

			case 2:	// LDA-602
					strcpy(HWName, sVNX2);
					HWMaxAttenuation = 1260;	// 63db max attenuation
					HWAttenuationStep = 10;		// .5db
					HWUnitScale = 5;			// .25db units
					break;

			case 3:	// LDA-302P-H
					strcpy(HWName, sVNX3);
					HWMaxAttenuation = 630;		// 31.5db max attenuation
					HWAttenuationStep = 10;		// .5 db steps
					HWUnitScale = 5;			// .25db units
					break;

			case 4:  // LDA-302P-1
					strcpy(HWName, sVNX4);
					HWMaxAttenuation = 1260;	// 63 db, expressed in .1 db units
					HWAttenuationStep = 20;		// 1 db steps
					HWUnitScale = 5;			// uses the standard .25 db encodin
					break;

			case 5:  // LDA-302P-2
					strcpy(HWName, sVNX5);
					HWMaxAttenuation = 1800;	// 90 db, expressed in .05db units, but the HW works in 1 db units!!
					HWAttenuationStep = 40;		// 2 db steps (expressed in .05db units, but the HW uses a lsb = 1db encoding)
					HWUnitScale = 20;			// the HW works in 1 db units!
					break;

			case 6:  // LDA-102-75
					strcpy(HWName, sVNX6);
					HWMaxAttenuation = 2400;	// 120 db, expressed in .05db units
					HWAttenuationStep = 10;		// .5 db steps (expressed in .05db units here, but the HW uses a lsb = .5db encoding)
					HWUnitScale = 10;			// the HW works in .5 db units!
					break;

			case 7:  // LDA-102E
					strcpy(HWName, sVNX7);
					HWMaxAttenuation = 2400;	// 120 db, expressed in .05db units, but the HW works in .5 db units!!
					HWAttenuationStep = 10;		// .5 db steps (expressed in .05db units here, but the HW uses a lsb = .5db encoding)
					HWUnitScale = 10;			// the HW works in .5 db units!
					break;

			case 8:  // LDA-602E
					strcpy(HWName, sVNX8);
					HWMaxAttenuation = 1900;	// 95 db, expressed in .25db units, but the HW works in .5 db units!!
					HWAttenuationStep = 10;		// .5 db steps (expressed in .05db units here, but the HW uses a lsb = .5db encoding)
					HWUnitScale = 10;			// the HW works in .5 db units!
					break;

			case 9:  // LDA-183
					strcpy(HWName, sVNX9);
					HWMaxAttenuation = 1260;	// 63 db, expressed in .05db units, but the HW works in .5 db units!!
					HWAttenuationStep = 10;		// .5 db steps (expressed in .05db units here, but the HW uses a lsb = .5db encoding)
					HWUnitScale = 10;			// the HW works in .5 db units!
					break;

			case 10:  // LDA-203
					strcpy(HWName, sVNX10);
					HWMaxAttenuation = 1260;	// 63 db, expressed in .05db units, but the HW works in .5 db units!!
					HWAttenuationStep = 10;		// .5 db steps (expressed in .25db units here, but the HW uses a lsb = .5db encoding)
					HWUnitScale = 10;			// the HW works in .5 db units!
					break;

			case 11:  // LDA-102EH
					strcpy(HWName, sVNX11);
					HWMaxAttenuation = 2400;	// 120 db, expressed in .05db units
					HWAttenuationStep = 2;		// .1 db steps (expressed in .05db units here)
					HWUnitScale = 1;			// the HW works in .05 db units!
					HWHasHiRes = TRUE;
					HWMaxProfileSaved = 50;
					HWMaxProfileRAM = 50;

					HWMinFrequency = 2000;		// 200 MHz
					HWMaxFrequency = 10000;		// 1 GHz
					break;

			case 12:  // LDA-602EH
					strcpy(HWName, sVNX12);
					HWMaxAttenuation = 2400;	// 120 db, expressed in .05db units
					HWAttenuationStep = 2;		// .1 db steps
					HWUnitScale = 1;			// the HW works in .05 db units!
					HWHasHiRes = TRUE;
					HWMaxProfileSaved = 50;
					HWMaxProfileRAM = 50;

					HWMinFrequency = 2000;		// 200 MHz
					HWMaxFrequency = 60000;		// 6 GHz
					break;

			case 13:  // LDA-602Q
					strcpy(HWName, sVNX13);
					HWMaxAttenuation = 2400;	// 120 db, expressed in .05db units
					HWAttenuationStep = 2;		// .1 db steps (expressed in .05db units here)
					HWUnitScale = 1;			// the HW works in .05 db units!
					HWHasHiRes = TRUE;
					HWMaxProfileSaved = 50;
					HWMaxProfileRAM = 50;

					HWMinFrequency = 2000;		// 200 MHz
					HWMaxFrequency = 60000;		// 6 GHz    03/29/19 - HME
					HWNumChannels = 4;
					HWEndpoint = ENDPOINT_INT_IN1;
					break;

			case 14:  // LDA-906V
					strcpy(HWName, sVNX14);
					HWMaxAttenuation = 1800;	// 90 db, expressed in .05db units
					HWAttenuationStep = 2;		// .1 db steps (expressed in .05db units here)
					HWUnitScale = 1;			// the HW works in .05 db units!
					HWHasHiRes = TRUE;
					HWMaxProfileSaved = 50;
					HWMaxProfileRAM = 50;

					HWMinFrequency = 2000;		// 200 MHz
					HWMaxFrequency = 60000;		// 6 GHz    03/29/19 - HME
					HWEndpoint = ENDPOINT_INT_IN1;
					break;

			case 15:  // LDA-133
					strcpy(HWName, sVNX15);
					HWMaxAttenuation = 1260;	// 63 db, expressed in .05db units
					HWAttenuationStep = 10;		// .5 db steps (expressed in .05db units here)
					HWUnitScale = 1;			// the HW works in .05 db units!
					HWHasHiRes = TRUE;
					HWMaxProfileSaved = 50;
					HWMaxProfileRAM = 50;

					HWMinFrequency = 60;		// 6 MHz
					HWMaxFrequency = 130000;	// 13 GHz
					HWEndpoint = ENDPOINT_INT_IN1;
					break;

			case 16:  // LDA-5018
					strcpy(HWName, sVNX16);
					HWMaxAttenuation = 1000;	// 50 db, expressed in .05db units
					HWAttenuationStep = 2;		// .1 db steps (expressed in .05db units here)
					HWUnitScale = 1;			// the HW works in .05 db units!
					HWHasHiRes = TRUE;
					HWMaxProfileSaved = 50;
					HWMaxProfileRAM = 50;

					HWMinFrequency = 500;		// 50 MHz
					HWMaxFrequency = 180000;	// 18 GHz
					HWEndpoint = ENDPOINT_INT_IN1;
					break;

			case 17:  // LDA-5040
					strcpy(HWName, sVNX17);
					HWMaxAttenuation = 1000;	// 50 db, expressed in .05db units
					HWAttenuationStep = 2;		// .1 db steps (expressed in .05db units here)
					HWUnitScale = 1;			// the HW works in .05 db units!
					HWHasHiRes = TRUE;
					HWMaxProfileSaved = 50;
					HWMaxProfileRAM = 1000;		// earlier versions don't support the extended profiles, we catch that later on

					HWMinFrequency = 200000;	// 20 GHz
					HWMaxFrequency = 400000;	// 40 GHz
					HWEndpoint = ENDPOINT_INT_IN1;
					break;

			case 18:  // LDA-906V-8
					strcpy(HWName, sVNX18);
					HWMaxAttenuation = 1800;	// 90 db, expressed in .05db units
					HWAttenuationStep = 2;		// .1 db steps (expressed in .05db units here)
					HWUnitScale = 1;			// the HW works in .05 db units
					HWHasHiRes = TRUE;
					HWMaxProfileSaved = 50;
					HWMaxProfileRAM = 1000;		// earlier versions don't support the extended profiles, we catch that later on

					HWMinFrequency = 2000;		// 200 MHz
					HWMaxFrequency = 60000;		// 6 GHz
					HWNumChannels = 8;
					HWEndpoint = ENDPOINT_INT_IN1;
					break;

			case 19:  // LDA-802EH
					strcpy(HWName, sVNX19);
					HWMaxAttenuation = 2400;	// 120 db, expressed in .05db units
					HWAttenuationStep = 2;		// .1 db steps (expressed in .05db units here)
					HWUnitScale = 1;			// the HW works in .05 db units
					HWHasHiRes = TRUE;
					HWMaxProfileSaved = 50;
					HWMaxProfileRAM = 1000;

					HWMinFrequency = 2000;		// 200 MHz
					HWMaxFrequency = 80000;		// 8 GHz
					HWEndpoint = ENDPOINT_INT_IN1;
					break;

			case 20:  // LDA-802Q
					strcpy(HWName, sVNX20);
					HWMaxAttenuation = 2400;	// 120 db, expressed in .05db units
					HWAttenuationStep = 2;		// .1 db steps (expressed in .05db units here)
					HWUnitScale = 1;			// the HW works in .05 db units
					HWHasHiRes = TRUE;
					HWMaxProfileSaved = 50;
					HWMaxProfileRAM = 1000;

					HWMinFrequency = 2000;		// 200 MHz
					HWMaxFrequency = 80000;		// 8 GHz
					HWNumChannels = 4;			// 4 channel 8GHz attenuator
					HWExpandable = true;
					HWEndpoint = ENDPOINT_INT_IN1;
					break;

			case 21:  // LDA-802-8
					strcpy(HWName, sVNX21);
					HWMaxAttenuation = 2400;	// 120 db, expressed in .05db units
					HWAttenuationStep = 2;		// .1 db steps (expressed in .05db units here)
					HWUnitScale = 1;			// the HW works in .05 db units
					HWHasHiRes = TRUE;
					HWMaxProfileSaved = 50;
					HWMaxProfileRAM = 1000;

					HWMinFrequency = 2000;		// 200 MHz
					HWMaxFrequency = 80000;		// 8 GHz
					HWNumChannels = 8;			// 8 channel 8GHz attenuator
					HWExpandable = true;
					HWEndpoint = ENDPOINT_INT_IN1;
					break;

			case 22:  // LDA-906V-4
					strcpy(HWName, sVNX22);
					HWMaxAttenuation = 2400;	// 120 db, expressed in .05db units
					HWAttenuationStep = 2;		// .1 db steps (expressed in .05db units here)
					HWUnitScale = 1;			// the HW works in .05 db units
					HWHasHiRes = TRUE;
					HWMaxProfileSaved = 50;
					HWMaxProfileRAM = 1000;

					HWMinFrequency = 2000;		// 200 MHz
					HWMaxFrequency = 60000;		// 6 GHz
					HWNumChannels = 4;			// 4 channel 6GHz attenuator
					HWEndpoint = ENDPOINT_INT_IN1;
					break;

			case 23:  // LDA-8X1-752
					strcpy(HWName, sVNX23);
					HWMaxAttenuation = 1800;	// 90 db, expressed in .05db units
					HWAttenuationStep = 2;		// .1 db steps (expressed in .05db units here)
					HWUnitScale = 1;			// the HW works in .05 db units
					HWHasHiRes = TRUE;
					HWMaxProfileSaved = 50;
					HWMaxProfileRAM = 1000;

					HWMinFrequency = 5000;		// 500 MHz
					HWMaxFrequency = 60000;		// 6 GHz
					HWNumChannels = 8;			// 8 channel 6GHz attenuator
					HWExpandable = true;
					HWEndpoint = ENDPOINT_INT_IN1;
					break;

			case 24:  // LDA-908V
					strcpy(HWName, sVNX24);
					HWMaxAttenuation = 1800;	// 90 db, expressed in .05db units
					HWAttenuationStep = 2;		// .1 db steps (expressed in .05db units here)
					HWUnitScale = 1;			// the HW works in .05 db units
					HWHasHiRes = TRUE;
					HWMaxProfileSaved = 50;
					HWMaxProfileRAM = 1000;

					HWMinFrequency = 10;		// 1 MHz
					HWMaxFrequency = 80000;		// 8 GHz
					HWNumChannels = 1;			// 1 channel 8GHz attenuator
					HWEndpoint = ENDPOINT_INT_IN1;
					break;

			case 25:  // LDA-908V-4
					strcpy(HWName, sVNX25);
					HWMaxAttenuation = 1800;	// 90 db, expressed in .05db units
					HWAttenuationStep = 2;		// .1 db steps (expressed in .05db units here)
					HWUnitScale = 1;			// the HW works in .05 db units
					HWHasHiRes = TRUE;
					HWMaxProfileSaved = 50;
					HWMaxProfileRAM = 1000;

					HWMinFrequency = 10;		// 1 MHz
					HWMaxFrequency = 80000;		// 8 GHz
					HWNumChannels = 4;			// 4 channel 8GHz attenuator
					HWExpandable = true;
					HWEndpoint = ENDPOINT_INT_IN1;
					break;

			case 26:  // LDA-908V-8
					strcpy(HWName, sVNX26);
					HWMaxAttenuation = 1800;	// 90 db, expressed in .05db units
					HWAttenuationStep = 2;		// .1 db steps (expressed in .05db units here)
					HWUnitScale = 1;			// the HW works in .05 db units
					HWHasHiRes = TRUE;
					HWMaxProfileSaved = 50;
					HWMaxProfileRAM = 1000;

					HWMinFrequency = 10;		// 1 MHz
					HWMaxFrequency = 80000;		// 8 GHz
					HWNumChannels = 8;			// 8 channel 8GHz attenuator
					HWExpandable = true;
					HWEndpoint = ENDPOINT_INT_IN1;
					break;

			case 27:  // LDA-608V-4
					strcpy(HWName, sVNX27);
					HWMaxAttenuation = 1200;	// 60 db, expressed in .05db units
					HWAttenuationStep = 2;		// .1 db steps (expressed in .05db units here)
					HWUnitScale = 1;			// the HW works in .05 db units
					HWHasHiRes = TRUE;
					HWMaxProfileSaved = 50;
					HWMaxProfileRAM = 1000;

					HWMinFrequency = 2000;		// 200 MHz
					HWMaxFrequency = 80000;		// 8 GHz
					HWNumChannels = 4;			// 4 channel 8GHz attenuator
					HWEndpoint = ENDPOINT_INT_IN1;
					break;

		} /* HWType switch */

		// Lets get the device's serial number so we can uniquely identify it
		// We can also use the USB BusNumber:Address to identify devices

		HWSerialNumber = GetSerialNumber(devhandle, HWType, desc.iSerialNumber, rcvbuff, &HWFeatures);

		// We should be done with the device now
		libusb_close(devhandle);

		if (HWSerialNumber < 0) continue;		// without a serial number we can't do anything with the device

		// find an open slot to save the data
		// lets see if we have this unit in our table of devices already
		// RD -- we use the device serial number in this phase, and USB bus:address during later opens to match up the device
		//
		bFound = FALSE;

		for (j = 1; j<MAXDEVICES; j++){
			if (lda[j].SerialNumber == HWSerialNumber) {
				// we already have the device in our table
				bFound = TRUE;
				// Even for multichannel devices, we can use channel 0 to see if we're connected
				lda[j].DevStatus[0] = lda[j].DevStatus[0] | DEV_CONNECTED; // its here, mark it as connected
				// at this point the device is present, but not in use, no sense looking more
				break;
			 }
			} // MAXDEVICES for loop

      } // open failed

      // if the device isn't in the table we need to add it
      if (!bFound) {
		hTemp = 0;
			for (i = 1; i<MAXDEVICES; i++) {
				if (lda[i].SerialNumber == 0) {
				hTemp = i;
				break;
				}
			} // end of for loop search for an empty slot in our array of devices

		/* save all of the data we've already acquired */
		if (hTemp) {
		  lda[hTemp].SerialNumber = HWSerialNumber;		            			// save the device's serial number
		  lda[hTemp].DevStatus[0] = lda[hTemp].DevStatus[0] | DEV_CONNECTED;	// mark it as present
		  strcpy (lda[hTemp].ModelName, HWName);     		    	    		// save the device's model name


		  if (lda[hTemp].SerialNumber == 0 || lda[hTemp].SerialNumber > 4300) {
			lda[hTemp].DevStatus[0] = lda[hTemp].DevStatus[0] | DEV_V2FEATURES;	// these devices have V2 firmware
		  }
		  else {
			lda[hTemp].DevStatus[0] = lda[hTemp].DevStatus[0] & ~DEV_V2FEATURES; // these don't
		  }

		  if (HWHasHiRes) { // Single channel HiRes, Quad and 8 channel HiRes LDAs
				lda[hTemp].DevStatus[0] = lda[hTemp].DevStatus[0] | DEV_HIRES; 	// HiRes LDAs
			  }
		  else {
				lda[hTemp].DevStatus[0] = lda[hTemp].DevStatus[0] & ~DEV_HIRES;	// everybody else
		  }

		  // It could be a 602Q in disguise  12/22/18
		  // We use lda[0] for device enumeration only
		  // RD -- NB HWFeatures is set by GetSerialNumber
		  if ((HWType == 11) && (HAS_4CHANNELS & HWFeatures)) {
			// It is indeed.
			HWType = 13;
			strcpy(HWName, sVNX13);
			strcpy (lda[hTemp].ModelName, HWName);  // save the device's model name
			// with the model change, we also have to make sure the device range is updated
			HWMinFrequency = 2000;			// 200 MHz
			HWMaxFrequency = 60000;			// 6 GHz
			HWNumChannels = 4;
			HWMaxProfileSaved = 50;
			HWMaxProfileRAM = 50;			// the early LDA-602Q devices do not support extended profiles
			HWEndpoint = ENDPOINT_INT_IN1;
		  }

		  // early LDA-906V-8 devices also do not support extended profiles
		  if ((HWType == LDA_906V_8) && (HWSerialNumber < 20000)) {

			  HWMaxProfileRAM = 50;
			  HWMaxProfileSaved = 50;
		  }

		  // -- save our device type
		  lda[hTemp].DevType = HWType;

		  // -- set up the default channel number, always 0 for single channel devices --
		  if (DEBUG_OUT > 2) printf("Saving %d as the number of channels in the base LDA device\r\n", HWNumChannels);
		  lda[hTemp].BaseChannels = HWNumChannels;  // save the number of channels in the base device
		  lda[hTemp].NumChannels = HWNumChannels;	// for non expandable devices NumChannels = BaseChannels. For expandable devices we update NumChannels during the device init.
		  if (HWNumChannels == 4) lda[hTemp].DevStatus[0] = lda[hTemp].DevStatus[0] | HAS_4CHANNELS;
		  if (HWNumChannels == 8) lda[hTemp].DevStatus[0] = lda[hTemp].DevStatus[0] | HAS_8CHANNELS;
		  lda[hTemp].Expandable = HWExpandable;

		  // Note - the channel value gets updated later for multi-channel devices
		  //	    the number of channels for expandable devices also gets updated later
		  lda[hTemp].Channel = 0;
		  lda[hTemp].ChMask = 0x01;

		  // initialize our cache data structures for this device to the unknown state
		  ClearDevCache(hTemp, HWNumChannels);

		  // -- copy over the rest of the device parameters --
		  lda[hTemp].MinAttenStep = HWAttenuationStep;
		  lda[hTemp].MinAttenuation = HWMinAttenuation;
		  lda[hTemp].MaxAttenuation = HWMaxAttenuation;		    // default values for attenuation range
		  lda[hTemp].UnitScale = HWUnitScale;				    // size of hardware unit in .05db units
																// .25db = 5, .05db = 1. .5db = 10, 1db = 20
		  lda[hTemp].MinFrequency = HWMinFrequency;
		  lda[hTemp].MaxFrequency = HWMaxFrequency;

		  lda[hTemp].ProfileMaxLength = HWMaxProfileRAM;
		  lda[hTemp].ProfileMaxSaved = HWMaxProfileSaved;

		  // The device has been closed so let's make sure we can find it again
		  // We use the BusAddress for opens by the user after detection
		  lda[hTemp].idVendor = desc.idVendor;
		  lda[hTemp].idProduct = desc.idProduct;
		  lda[hTemp].idType = HWType;
		  lda[hTemp].BusAddress = HWBusAddress;
		  lda[hTemp].Endpoint = HWEndpoint;
		  strcpy(lda[hTemp].Serialstr, rcvbuff);

		  if (DEBUG_OUT > 1) {
			printf("Stored as new device #%d\r\n", hTemp);
			printf("Serial number=%d\r\n", lda[hTemp].SerialNumber);
			printf("Devstatus=%08x\r\n", lda[hTemp].DevStatus[0]);
			printf("Model name=%s\r\n", lda[hTemp].ModelName);
			printf("MinAttenuation=%d\r\n", lda[hTemp].MinAttenuation);
			printf("MaxAttenuation=%d\r\n", lda[hTemp].MaxAttenuation);
			printf("Resolution=%d\r\n", lda[hTemp].MinAttenStep);
			printf("Vendor ID=%04x\r\n", lda[hTemp].idVendor);
			printf("Product ID=%04x\r\n", lda[hTemp].idProduct);
			printf("Serial number=%s\r\n", lda[hTemp].Serialstr);
			printf("Number of channels=%d\r\n", lda[hTemp].NumChannels);
			printf("Profile Length=%d\r\n", lda[hTemp].ProfileMaxLength);
		  }
		}
		else {
			// our table of devices is full, not much we can do

		}
      } /* if !bfound  */
    } // if HWtype
  }	// end of for loop over the device list

  // -- we can free the list of devices now --
  //    this call may free individual device data objects that don't have an open handle to them, but it should not free
  //    the device structures for active (open) devices
  //
  libusb_free_device_list(devlist, 1);		// RD 4-2019 not devs, the devlist!

  /* clean up the structure and mark unused slots */
  for (i = 1; i<MAXDEVICES; i++){
    if ((lda[i].SerialNumber != 0) && !(lda[i].DevStatus[0] & DEV_CONNECTED))
      lda[i].SerialNumber = 0;	// mark this slot as unused

    if (lda[i].SerialNumber == 0)
      lda[i].DevStatus[0] = 0;		// clear the status for empty slots
  }	// end of zombie removal for loop

}

/* ----------------------------------------------------------------- */

void fnLDA_Init(void) {
  /* clear out the storage structure. Must be called once before anything else */
  int i,j;
  int status;
  int usb_status;

  for (i = 0; i<MAXDEVICES; i++){
	  ClearDevCache(i, CHANNEL_MAX);  // clear all cached variables to the unknown state

    for (j = 0; j<CHANNEL_MAX; j++) {
      lda[i].DevStatus[j] = 0;		// init to no devices connected, clear per channel status

	}
    lda[i].SerialNumber = 0;		// clear the serial number
    lda[i].ModelName[0] = 0;		// put a null string in each model name field
    lda[i].DevHandle = 0;			// clear the device handles
  }

  if (!Did_libusb_init){
	usb_status = libusb_init(&LDA_ctx);				// ensure we only call libusb_init once
	if (usb_status != 0){
		if (DEBUG_OUT > 0)  printf("libusb_init failed with usb_status = %d\r\n", usb_status);
		return;					// not easy to recover from this...
 }
	Did_libusb_init = TRUE;
  }

	// check what timer resources are available
	if (clock_getres(CLOCK_MONOTONIC_COARSE, &mtimer_CTime )){
		if (DEBUG_OUT > 0)  printf("Unable to use HR timer, error number %d\r\n", errno);
		HasHRTimer = FALSE;
	}
	else {
		HasHRTimer = TRUE;
		if (DEBUG_OUT > 0)  printf("HR timer resolution = %ld in nanoseconds\r\n", mtimer_CTime.tv_nsec);
		clock_gettime(CLOCK_MONOTONIC_COARSE, &mtimer_LastSend);
	}

  //  libusb_set_option(LDA_ctx, LIBUSB_OPTION_LOG_LEVEL, verbose);
  if (DEBUG_OUT > 0)  printf("library version %s\r\n", fnLDA_LibVersion());
}

void fnLDA_SetTestMode(bool testmode) {
  TestMode = testmode;
}


int fnLDA_GetLibVersion()
    {
        return LDA_DLLVERSION;
    }


// Return the current device status, may be called before a device is opened (via fnLDA_InitDevice)
// The function returns the status for the currently selected channel, adding in the device wide status bits stored in channel 0 for multi-channel devices
// Note that only some status bits are valid for an unopened device
//
int fnLDA_GetDeviceStatus(DEVID deviceID) {
	int ChannelStatusMask = (PROFILE_ACTIVE | SWP_BIDIRECTIONAL | SWP_REPEAT | SWP_UP | SWP_ACTIVE);
	int ch = 0;

       if (deviceID >= MAXDEVICES){
		return INVALID_DEVID;
	}
	else
	{
		if (lda[deviceID].NumChannels > 1){
			ch = lda[deviceID].Channel;
			return (lda[deviceID].DevStatus[0] & ~ChannelStatusMask) | (lda[deviceID].DevStatus[ch] & ChannelStatusMask);
		}
		else
		  return lda[deviceID].DevStatus[0];
	}
    }



//	The tracing features are intended to assist in debugging --
//	Note that DEBUG related #defines can alter the behavior of the debug printf code
//	IOTrace is used to control the debugging messages from the underlying libusb library
//	Review its documentation, and .h files for more detail
//	Note that libusb debug message behavior varies depending on the version of libusb being used.
//  If you want to use IO_Trace != 0 with libusb output, then call this function after fnLDA_Init

void fnLDA_SetTraceLevel(int trace, int devtrace, bool verbose)
    {
			const struct libusb_version * libversion = NULL;

			// --- enable libusb tracing using IO_Trace value---
      bVerbose = verbose;

      // --- general purpose tracing ---
      Trace_Out = trace;

      // --- device IO tracing ---
      IO_Trace = devtrace;

			if (bVerbose == TRUE ) {
	 			// enable libusb tracing
				libversion = libusb_get_version(); // RD!! The get_version function is not supported by FC19 libusb
				if (libversion != NULL) {

				if ((libversion->major == 1) && (libversion->micro >= 22)) {
				//	libusb_set_option(LDA_ctx, LIBUSB_OPTION_LOG_LEVEL, IO_Trace);
				}
				else {
					libusb_set_debug(LDA_ctx, IO_Trace);
					if (DEBUG_OUT > 1) printf("Returned from libusb_set_debug %d\r\n",IO_Trace);

				}
				if (DEBUG_OUT > 1) printf("Using Libusb version %d.%d.%d\r\n",libversion->major, libversion->minor, libversion->micro);
				}
			}
    }



int fnLDA_GetNumDevices() {
  int retval = 0;
  int NumDevices = 0;
  int i;

  // See how many devices we can find, or have found before
  if (TestMode){
    // construct a fake device

    lda[1].SerialNumber = 55102;
    lda[1].DevStatus[0] = lda[1].DevStatus[0] | DEV_CONNECTED;
    lda[1].idType = 1;
    lda[1].MinAttenuation = 0;		// 0db is always the minimum
    lda[1].MaxAttenuation = 1260;	// 63 db in .05db units
    lda[1].MinAttenStep = 10;		// .5db resolution of the attenuator
    lda[1].UnitScale = 5;		// for consistency...
    lda[1].NumChannels = 1;		// most devices have 1 channel

    strcpy (lda[1].ModelName, "LDA-102");

    // construct a second fake device
    lda[2].SerialNumber = 55602;
    lda[2].DevStatus[0] = lda[2].DevStatus[0] | DEV_CONNECTED;
    lda[2].idType = 2;
    lda[2].MinAttenuation = 0;		// 0db is always the minimum
    lda[2].MaxAttenuation = 1260;	// 63 db in .05db units
    lda[2].MinAttenStep = 10;		// .5db resolution of the attenuator
    lda[2].UnitScale = 5;		// for consistency...
    lda[2].NumChannels = 1;		// most devices have 1 channel

    strcpy (lda[2].ModelName, "LDA-602");

    retval = 2;

  } else {
    // go look for some real hardware
    FindVNXDevices();

    // Total up the number of devices we have
    for (i = 1; i < MAXDEVICES; i++){
      if (lda[i].DevStatus[0] & DEV_CONNECTED) NumDevices++;
    }
    retval = NumDevices;

  }
  return retval;
}

int fnLDA_GetNumChannels(DEVID deviceID) {
  if (deviceID >= MAXDEVICES)
    return INVALID_DEVID;

  return lda[deviceID].NumChannels;
}

int fnLDA_GetDevInfo(DEVID *ActiveDevices) {
  int i;
  int NumDevices = 0;

  if ( ActiveDevices == NULL) return 0; // bad array pointer, no place to put the DEVIDs

  for (i = 1; i < MAXDEVICES; i++){ // NB -- we never put an active device in lda[0] - so DEVIDs start at 1
    if (lda[i].DevStatus[0] & DEV_CONNECTED) {
      ActiveDevices[NumDevices] = i;
      NumDevices++;
    }
  }

  return NumDevices;
}

int fnLDA_GetModelName(DEVID deviceID, char *ModelName) {
  int NumChars = 0;

  if (deviceID >= MAXDEVICES){
    return 0;
  }

  if(lda[deviceID].DevType == 21) // LDA-802-Device -- Expansion bus channel #
  {
  	   sprintf(lda[deviceID].ModelName, "LDA-802-%d", lda[deviceID].NumChannels);
  }

  NumChars = strlen(lda[deviceID].ModelName);
  // If NULL result pointer, just return the number of chars in the name
  if ( ModelName == NULL) return NumChars;
  strcpy(ModelName, lda[deviceID].ModelName);

  return NumChars;
}

// InitDevice opens the device for use. To better support code that opens and closes the device a lot
// only a few key parameters are read during the open. The rest are read as needed.
// For expandable devices the number of channels is updated here, and the active channel is set to 0 for all multi-channel devices.

int fnLDA_InitDevice(DEVID deviceID) {

	LVSTATUS status;

  if ((deviceID >= MAXDEVICES) || (deviceID == 0)) {
    return INVALID_DEVID;
  }

  if (TestMode)
    lda[deviceID].DevStatus[0] = lda[deviceID].DevStatus[0] | DEV_OPENED;
  else {

    // Go ahead and open a handle to the hardware
    status = VNXOpenDevice(deviceID);

	if (status != STATUS_OK)  // VNXOpenDevice returns 0 (STATUS_OK) if the open succeeded, otherwise an error code
	  return status;

	// RD Test of sends before read thread has actually Started
	catnap(10);

	// for expandable devices find out how many channels we have
	if (lda[deviceID].Expandable) {
		if (!GetChannel(deviceID)) {
			return BAD_HID_IO;
		}
	}

    // Get the rest of the device parameters from the device
    if (DEBUG_OUT > 0) printf("Time to start getting parameters from device %d\r\n", deviceID);
    if (!GetAttenuation(deviceID)) {
	return BAD_HID_IO;
    }


    if (DEBUG_OUT > 1) printf("about to get Working Frequency\r\n");
    if (lda[deviceID].DevStatus[0] & DEV_HIRES) {		// for HiRes devices get the current working frequency
	 if (!GetFrequency(deviceID)) {
	   return BAD_HID_IO;
	 }
    }

  } // end of real device open process case

  // if we got here everything worked OK
  return STATUS_OK;
}


int fnLDA_CloseDevice(DEVID deviceID) {

	int i;
    bool DevOpen = FALSE;
    int Trace_Save = 0;            // we save the global trace state and use Trace_Init for device init/de-init

  if (deviceID >= MAXDEVICES)
    return INVALID_DEVID;

  if (!CheckDeviceOpen(deviceID))
    return DEVICE_NOT_READY;

  if (TestMode)
    lda[deviceID].DevStatus[0] = lda[deviceID].DevStatus[0] & ~DEV_OPENED;
  else {

    // we use IO_Trace for the device init and close phases
    Trace_Save = Trace_Out;
    Trace_Out = IO_Trace;

    // Go ahead and close this hardware - the first step is to stop its read thread
    lda[deviceID].thread_command = THREAD_EXIT;

    // The thread handler will clear any pending reads on the device. We'll wait up to 1 second then give up.
		// RD 9/23/20 testing a longer wait
    int retries;
    retries = 100;
    while (retries && (lda[deviceID].thread_command != THREAD_DEAD)) {
      catnap(100);  /* wait 100 ms */
      retries--;
    }
    if (DEBUG_OUT > 2) printf("After telling the thread to close, we have thread_command=%d retries=%d\r\n", lda[deviceID].thread_command, retries);
    lda[deviceID].thread_command = THREAD_EXIT; // this command should never be read ...

	libusb_close(lda[deviceID].DevHandle);	// -- RD 4/2019 re-designed to handle libusb open/close interaction in the API threads instead of in the read thread

    // Mark it closed in our list of devices
    lda[deviceID].DevStatus[0] = lda[deviceID].DevStatus[0] & ~DEV_OPENED;
  }

   // if no devices remain open, then we should be able to call libusb's exit procedure.
  for (i = 1; i<MAXDEVICES; i++){
    if (lda[i].DevStatus[0] & DEV_OPENED) {
        DevOpen = TRUE;                             // we found an open LDA device, not time to clean up yet
        break;
     }
  }

  if (!DevOpen){
    // we don't have any open LDA devices, so we should be able to safely close libusb. We will re-open it on the next GetNumDevices, or device open
    if (Did_libusb_init){
		libusb_exit(LDA_ctx);		// close our libusb context
		Did_libusb_init = FALSE;
	}
  }

  Trace_Out = Trace_Save;            // restore the normal trace level
  return STATUS_OK;

}

int fnLDA_GetSerialNumber(DEVID deviceID) {
  if (deviceID >= MAXDEVICES)
    return INVALID_DEVID;

  return lda[deviceID].SerialNumber;
}


// Functions to set parameters

// Set the attenuator - the function now uses .05db units
LVSTATUS fnLDA_SetAttenuation(DEVID deviceID, int attenuation) {

	int tmp_attenuation = 0;

	if (deviceID >= MAXDEVICES)
	  return INVALID_DEVID;

	if (!CheckDeviceOpen(deviceID))
	  return DEVICE_NOT_READY;

	LockDev(deviceID, TRUE);

	int old_attenuation = lda[deviceID].Attenuation[lda[deviceID].Channel];

	if ((attenuation >= lda[deviceID].MinAttenuation) && (attenuation <= lda[deviceID].MaxAttenuation)) {
	  lda[deviceID].Attenuation[lda[deviceID].Channel] = attenuation;
	  if (TestMode){
	    LockDev(deviceID, FALSE);
	    return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW
	  }
	} else {
	  LockDev(deviceID, FALSE);
	  return BAD_PARAMETER;
	}

	// the attenuation value is OK, lets send it to the hardware

	if (lda[deviceID].UnitScale > 0)
		tmp_attenuation = lda[deviceID].Attenuation[lda[deviceID].Channel] / lda[deviceID].UnitScale;

	unsigned char *ptr = (unsigned char *) &tmp_attenuation;

	if (!SendReport(deviceID, VNX_PWR | VNX_SET, ptr, 4)) {
	  lda[deviceID].Attenuation[lda[deviceID].Channel] = old_attenuation;
	  LockDev(deviceID, FALSE);
	  return BAD_HID_IO;
	}

	LockDev(deviceID, FALSE);
	return STATUS_OK;
}


// Set the attenuator for a quad (or other multi-channel) attenuator- the function uses .05db units
//
LVSTATUS fnLDA_SetAttenuationQ(DEVID deviceID, int attenuation, int channel) {

	int tmp_attenuation = 0;
	LVSTATUS SCResult;

	if (deviceID >= MAXDEVICES)
	  return INVALID_DEVID;

	if (!CheckDeviceOpen(deviceID))
	  return DEVICE_NOT_READY;

	// set the channel first
	SCResult = (LVSTATUS) SetChannel(deviceID, channel);
		if ( SCResult != STATUS_OK) return SCResult;

	return fnLDA_SetAttenuation(deviceID, attenuation);
}

// Set the attenuation ramp start value
LVSTATUS fnLDA_SetRampStart(DEVID deviceID, int rampstart) {
	int tmp_rampstart = 0;

	if (deviceID >= MAXDEVICES)
	  return INVALID_DEVID;

	if (!CheckDeviceOpen(deviceID))
	  return DEVICE_NOT_READY;

	int old_rampstart = lda[deviceID].RampStart[lda[deviceID].Channel];

	if ((rampstart >= lda[deviceID].MinAttenuation) && (rampstart <= lda[deviceID].MaxAttenuation)) {
	  lda[deviceID].RampStart[lda[deviceID].Channel] = rampstart;
	  if (TestMode){
	    return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW
	  }
	} else {
	  return BAD_PARAMETER;
	}

	// the ramp start value is OK, lets send it to the hardware
	if (lda[deviceID].UnitScale > 0)
		tmp_rampstart = lda[deviceID].RampStart[lda[deviceID].Channel] / lda[deviceID].UnitScale;

	unsigned char *ptr = (unsigned char *) &tmp_rampstart;

	if (!SendReport(deviceID, VNX_ASTART | VNX_SET, ptr, 4)){
	  lda[deviceID].RampStart[lda[deviceID].Channel] = old_rampstart;
	  return BAD_HID_IO;
	}

	return STATUS_OK;
}

// Set the attenuation ramp end value
LVSTATUS fnLDA_SetRampEnd(DEVID deviceID, int rampstop) {
	int tmp_rampstop = 0;

	if (deviceID >= MAXDEVICES)
	  return INVALID_DEVID;

	if (!CheckDeviceOpen(deviceID))
	  return DEVICE_NOT_READY;

	int old_rampstop = lda[deviceID].RampStop[lda[deviceID].Channel];

	if ((rampstop >= lda[deviceID].MinAttenuation) && (rampstop <= lda[deviceID].MaxAttenuation)){

	  lda[deviceID].RampStop[lda[deviceID].Channel] = rampstop;
	  if (TestMode)
	    return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW
	} else {
	  return BAD_PARAMETER;
	}
	// the ramp end value is OK, lets send it to the hardware
	if (lda[deviceID].UnitScale > 0)
	  tmp_rampstop = lda[deviceID].RampStop[lda[deviceID].Channel] / lda[deviceID].UnitScale;

	unsigned char *ptr = (unsigned char *) &tmp_rampstop;

	if (!SendReport(deviceID, VNX_ASTOP | VNX_SET, ptr, 4)) {
	  lda[deviceID].RampStop[lda[deviceID].Channel] = old_rampstop;
	  return BAD_HID_IO;
	}

	return STATUS_OK;
}

// Set the attenuation ramp step size for the first phase
LVSTATUS fnLDA_SetAttenuationStep(DEVID deviceID, int attenuationstep) {
	int tmp_attenuationstep = 0;

	if (deviceID >= MAXDEVICES)
	  return INVALID_DEVID;

	if (!CheckDeviceOpen(deviceID))
	  return DEVICE_NOT_READY;

	int old_attenuationstep = lda[deviceID].AttenuationStep[lda[deviceID].Channel];

	if (attenuationstep < (lda[deviceID].MaxAttenuation - lda[deviceID].MinAttenuation)) {
	  lda[deviceID].AttenuationStep[lda[deviceID].Channel] = attenuationstep;
	  if (TestMode)
	    return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW
	} else {
	  return BAD_PARAMETER;
	}
	// the attenuation ramp step value is OK, lets send it to the hardware
	if (lda[deviceID].UnitScale > 0)
		tmp_attenuationstep = lda[deviceID].AttenuationStep[lda[deviceID].Channel] / lda[deviceID].UnitScale;

	unsigned char *ptr = (unsigned char *) &tmp_attenuationstep;

	if (!SendReport(deviceID, VNX_ASTEP | VNX_SET, ptr, 4)) {
		lda[deviceID].AttenuationStep[lda[deviceID].Channel] = old_attenuationstep;
		return BAD_HID_IO;
	}

	return STATUS_OK;
}

// Set the attenuation ramp step size for the second phase of the ramp
//
LVSTATUS fnLDA_SetAttenuationStepTwo(DEVID deviceID, int attenuationstep2) {
	int tmp_attenuationstep = 0;

	if (deviceID >= MAXDEVICES)
	  return INVALID_DEVID;

	if (!CheckDeviceOpen(deviceID))
	  return DEVICE_NOT_READY;

	int old_attenuationstep = lda[deviceID].AttenuationStep2[lda[deviceID].Channel];

	if (attenuationstep2 < (lda[deviceID].MaxAttenuation - lda[deviceID].MinAttenuation)) {
	  lda[deviceID].AttenuationStep2[lda[deviceID].Channel] = attenuationstep2;
	  if (TestMode)
	    return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW
	} else {
	  return BAD_PARAMETER;
	}
	// the attenuation ramp step value is OK, lets send it to the hardware
	if (lda[deviceID].UnitScale > 0)
		tmp_attenuationstep = lda[deviceID].AttenuationStep2[lda[deviceID].Channel] / lda[deviceID].UnitScale;

	unsigned char *ptr = (unsigned char *) &tmp_attenuationstep;

	if (!SendReport(deviceID, VNX_ASTEP2 | VNX_SET, ptr, 4)) {
		lda[deviceID].AttenuationStep2[lda[deviceID].Channel] = old_attenuationstep;
		return BAD_HID_IO;
	}
	return STATUS_OK;
}

// Functions to set parameters
// Channel is 1 to 4 or 1 to 8, or 1 to N for expandable devices at the API level

int SetChannel(DEVID deviceID, int channel)
{
	char VNX_command[] = { 0, 0, 0, 0, 0, 0 };

    if (deviceID >= MAXDEVICES){
    return INVALID_DEVID;
  }

  if (!CheckDeviceOpen(deviceID)){
    return DEVICE_NOT_READY;
  }

  if (lda[deviceID].NumChannels < 2){  // Devices with only 1 channel won't accept this command but it's instructive to the reader and made redundant a few lines later anyway.
    return BAD_PARAMETER;
  }

  if (channel < 1 || channel > lda[deviceID].NumChannels){
    return BAD_PARAMETER;
  }
	// we use zero based channel numbers internally, the API is 1 based, as is the new format command
	lda[deviceID].Channel = channel - 1;
	lda[deviceID].GlobalChannel = channel - 1;

  if (lda[deviceID].Expandable && (channel > (lda[deviceID].BaseChannels))) {
		// the channel is located on an expansion module so we use the new format command
		VNX_command[0] = (char)channel;
  }
  else {
	// convert to our mask form and save the mask
	lda[deviceID].ChMask = 0x01 << lda[deviceID].Channel;
    VNX_command[5] = (char)lda[deviceID].ChMask;

  }
    // -- send the channel selection to the attenuator --
    if (!SendReport(deviceID, VNX_CHANNEL | VNX_SET, VNX_command, 6))
      return BAD_HID_IO;

  return STATUS_OK;
}

LVSTATUS fnLDA_SetChannel(DEVID deviceID, int channel)
{
	LVSTATUS result;
	result = SetChannel(deviceID, channel);
	if(!result)
	{
		usleep(700000);  // 700msec
	}

	return result;
}


// Set the working frequency for HiRes LDA devices, frequency is in 100KHz units
LVSTATUS fnLDA_SetWorkingFrequency(DEVID deviceID, int frequency) {
	int tmp_frequency = 0;
	int ch = 0;

	if (deviceID >= MAXDEVICES)
	  return INVALID_DEVID;

	if (!CheckDeviceOpen(deviceID))
	  return DEVICE_NOT_READY;

	if (!CheckHiRes(deviceID)) return BAD_PARAMETER;			// only HiRes devices have a working frequency
	if (lda[deviceID].DevType == LDA_133) return BAD_PARAMETER; // LDA-133 does not have a working frequency

	ch = lda[deviceID].Channel;

	int old_frequency = lda[deviceID].WorkingFrequency[ch];

	if (DEBUG_OUT > 0) {
	  printf("Min frequency is %d\r\n", lda[deviceID].MinFrequency);
	  printf("Max frequency is %d\r\n", lda[deviceID].MaxFrequency);
	  printf("User frequency to set is %d\r\n", frequency);
	}

	if ((frequency >= lda[deviceID].MinFrequency) && (frequency <= lda[deviceID].MaxFrequency)) {
	  lda[deviceID].WorkingFrequency[ch] = frequency;
	  if (TestMode)
	    return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW
	} else {
	  return BAD_PARAMETER;
	}

	// the working frequency value is OK, lets send it to the hardware
	tmp_frequency = frequency;
	unsigned char *ptr = (unsigned char *) &tmp_frequency;

	if (DEBUG_OUT > 0) printf("setting frequency to %d\r\n", tmp_frequency);

	if (!SendReport(deviceID, VNX_FREQUENCY | VNX_SET, ptr, 4)) {
	  lda[deviceID].WorkingFrequency[ch] = old_frequency;
	  return BAD_HID_IO;
	}

	return STATUS_OK;
}

// Set the time to dwell at each step on the first phase of the ramp
LVSTATUS fnLDA_SetDwellTime(DEVID deviceID, int dwelltime) {
	int ch = 0;

	if (deviceID >= MAXDEVICES)
	  return INVALID_DEVID;

	if (!CheckDeviceOpen(deviceID))
	  return DEVICE_NOT_READY;

	ch = lda[deviceID].Channel;

	int old_dwelltime = lda[deviceID].DwellTime[ch];

	if (dwelltime >= VNX_MIN_DWELLTIME) {
	  lda[deviceID].DwellTime[ch] = dwelltime;
	  if (TestMode)
	    return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW
	} else {
	  return BAD_PARAMETER;
	}

	// the dwell time value is OK, lets send it to the hardware
	unsigned char *ptr = (unsigned char *) &lda[deviceID].DwellTime;

	if (!SendReport(deviceID, VNX_ADWELL | VNX_SET, ptr, 4)) {
	  lda[deviceID].DwellTime[ch] = old_dwelltime;
	  return BAD_HID_IO;
	}

	return STATUS_OK;
}

// Set the time to dwell at each step on the second phase of the ramp
LVSTATUS fnLDA_SetDwellTimeTwo(DEVID deviceID, int dwelltime2) {
	int ch = 0;
	if (deviceID >= MAXDEVICES)
	  return INVALID_DEVID;

	if (!CheckDeviceOpen(deviceID))
	  return DEVICE_NOT_READY;

	ch = lda[deviceID].Channel;

	int old_dwelltime2 = lda[deviceID].DwellTime2[ch];

	if (dwelltime2 >= VNX_MIN_DWELLTIME) {
	  lda[deviceID].DwellTime2[ch] = dwelltime2;
	  if (TestMode)
	    return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW
	} else {
	  return BAD_PARAMETER;
	}

	// the dwell time value is OK, lets send it to the hardware
	unsigned char *ptr = (unsigned char *) &lda[deviceID].DwellTime2[ch];

	if (!SendReport(deviceID, VNX_ADWELL2 | VNX_SET, ptr, 4)) {
	  lda[deviceID].DwellTime2[ch] = old_dwelltime2;
	  return BAD_HID_IO;
	}

	return STATUS_OK;
}


// Set the time to wait at the end of the ramp
LVSTATUS fnLDA_SetIdleTime(DEVID deviceID, int idletime) {
	int ch = 0;

	if (deviceID >= MAXDEVICES)
	  return INVALID_DEVID;

	if (!CheckDeviceOpen(deviceID))
	  return DEVICE_NOT_READY;

	ch = lda[deviceID].Channel;

	int old_idletime = lda[deviceID].IdleTime[ch];

	if (idletime >= VNX_MIN_DWELLTIME) {
	  lda[deviceID].IdleTime[ch] = idletime;
	  if (TestMode)
	    return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW
	} else {
	  return BAD_PARAMETER;
	}

	// the idle time value is OK, lets send it to the hardware
	unsigned char *ptr = (unsigned char *) &lda[deviceID].IdleTime[ch];

	if (!SendReport(deviceID, VNX_AIDLE | VNX_SET, ptr, 4)) {
	  lda[deviceID].IdleTime[ch] = old_idletime;
	  return BAD_HID_IO;
	}

	return STATUS_OK;
}

// Set the time to wait between the first and second phase of a bidirectional ramp
LVSTATUS fnLDA_SetHoldTime(DEVID deviceID, int holdtime) {
	int ch = 0;

	if (deviceID >= MAXDEVICES)
	  return INVALID_DEVID;

	if (!CheckDeviceOpen(deviceID))
	  return DEVICE_NOT_READY;

	ch = lda[deviceID].Channel;

	int old_holdtime = lda[deviceID].HoldTime[ch];

	if (holdtime >= 0) {		// holdtime can be zero
	  lda[deviceID].HoldTime[ch] = holdtime;
	  if (TestMode)
	    return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW
	} else {
	  return BAD_PARAMETER;
	}

	// the hold time value is OK, lets send it to the hardware
	unsigned char *ptr = (unsigned char *) &lda[deviceID].HoldTime[ch];

	if (!SendReport(deviceID, VNX_AHOLD | VNX_SET, ptr, 4)) {
	  lda[deviceID].HoldTime[ch] = old_holdtime;
	  return BAD_HID_IO;
	}

	return STATUS_OK;
}


// Set the RF output on or off (not supported by all attenuators)
LVSTATUS fnLDA_SetRFOn(DEVID deviceID, bool on) {
	int ch = 0;

	if (deviceID >= MAXDEVICES)
	  return INVALID_DEVID;

	if (!CheckDeviceOpen(deviceID))
	  return DEVICE_NOT_READY;

	ch = lda[deviceID].Channel;

	char VNX_command[] = {0, 0, 0, 0};

	if (on) {
	  lda[deviceID].Modebits[ch] = lda[deviceID].Modebits[ch] & ~MODE_RFON;
	  lda[deviceID].Modebits[ch] = lda[deviceID].Modebits[ch] | MODE_RFON;
	  VNX_command[0] = 1;
	} else 	{
	  lda[deviceID].Modebits[ch] = lda[deviceID].Modebits[ch] & ~MODE_RFON;
	  VNX_command[0] = 0;
	}

	if (TestMode)
	  return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW

	if (!SendReport(deviceID, VNX_RFMUTE | VNX_SET, VNX_command, 1))
	  return BAD_HID_IO;

	return STATUS_OK;
}


// Set the ramp direction -- "up" is TRUE to ramp upwards
LVSTATUS fnLDA_SetRampDirection(DEVID deviceID, bool up) {
	int ch = 0;

	if (deviceID >= MAXDEVICES)
	  return INVALID_DEVID;

	if (!CheckDeviceOpen(deviceID))
	  return DEVICE_NOT_READY;

	ch = lda[deviceID].Channel;

	if (up)
	  lda[deviceID].Modebits[ch] = lda[deviceID].Modebits[ch] & ~SWP_DIRECTION;	// ramp or sweep direction up (bit == 0)
	else
	  lda[deviceID].Modebits[ch] = lda[deviceID].Modebits[ch] | SWP_DIRECTION;	// ramp or sweep direction downwards


	return STATUS_OK;
}

// Set the ramp mode -- mode = TRUE for repeated ramp, FALSE for one time ramp
LVSTATUS fnLDA_SetRampMode(DEVID deviceID, bool mode) {
	int ch = 0;

	if (deviceID >= MAXDEVICES)
	  return INVALID_DEVID;

	if (!CheckDeviceOpen(deviceID))
	  return DEVICE_NOT_READY;

	ch = lda[deviceID].Channel;

	if (mode) {
	  lda[deviceID].Modebits[ch] = lda[deviceID].Modebits[ch] | SWP_CONTINUOUS;		// Repeated ramp or sweep
	  lda[deviceID].Modebits[ch] = lda[deviceID].Modebits[ch] & ~SWP_ONCE;
	} else {
	  lda[deviceID].Modebits[ch] = lda[deviceID].Modebits[ch] | SWP_ONCE;			// one time ramp or sweep
	  lda[deviceID].Modebits[ch] = lda[deviceID].Modebits[ch] & ~SWP_CONTINUOUS;
	}

	return STATUS_OK;
}

// Select bidirectional or unidirectional ramp mode
// TRUE for bidirectional ramp, FALSE for unidirectional ramp
LVSTATUS fnLDA_SetRampBidirectional(DEVID deviceID, bool bidir_enable) {
	int ch = 0;

	if (deviceID >= MAXDEVICES){
		return INVALID_DEVID;
	}

	if (!CheckDeviceOpen(deviceID)){
		return DEVICE_NOT_READY;
	}

	if (!CheckV2Features(deviceID)){		// only attenuators with V2 firmware support bi-directional ramps
		return FEATURE_NOT_SUPPORTED;
	}

	ch = lda[deviceID].Channel;

	if (bidir_enable)
	{
		lda[deviceID].Modebits[ch] = lda[deviceID].Modebits[ch] | SWP_BIDIR;	// bidirectional ramp
	}
	else
	{
		lda[deviceID].Modebits[ch] = lda[deviceID].Modebits[ch] & ~SWP_BIDIR;	// unidirectional ramp
	}

	return STATUS_OK;
}



// Start the ramp
LVSTATUS fnLDA_StartRamp(DEVID deviceID, bool go) {
	int icount;
	int ch = 0;

	if (deviceID >= MAXDEVICES)
	  return INVALID_DEVID;

	if (!CheckDeviceOpen(deviceID))
	  return DEVICE_NOT_READY;

	ch = lda[deviceID].Channel;

	char VNX_ramp[] = {0, 0, 0, 0, 0, 0};

if (go)
	{
		if (CheckV2Features(deviceID)){
			icount = 3;														// the new attenuators use 3 bytes
			VNX_ramp[0] = (char) lda[deviceID].Modebits[ch] & MODE_SWEEP;	// mode sweep for V2 includes the 0x10 bit
		}
		else{
			icount = 1;														// the old attenuators use 1 byte
			VNX_ramp[0] = (char) lda[deviceID].Modebits[ch] & 0x0f;			// mask off the bidirectional bit
		}
	}
	else
	{
		if (CheckV2Features(deviceID)){
			icount = 3;
		}
		else{
			icount = 1;
		}

		VNX_ramp[0] = 0;
	}

	if (TestMode)
	  return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW

	if (DEBUG_OUT > 0) printf(" sending a ramp command = %x\n", VNX_ramp[0] );

	if (!SendReport(deviceID, VNX_SWEEP | VNX_SET, VNX_ramp, icount))
	  return BAD_HID_IO;

	return STATUS_OK;
}

// --- Start a ramp on multiple channels ---
//
// chmask has a 1 bit set for every channel to be started
// mode is composed of the sweep command byte flags SWP_DIRECTION, SWP_CONTINUOUS, SWP_ONCE, and SWP_BIDIR
// the deferred flag is not supported, but reserved for future use. Set it to false when calling this function for compatibility with future library versions
//
LVSTATUS fnLDA_StartRampMC(DEVID deviceID, int mode, int chmask, bool deferred)
{
	int icount;
	unsigned int ch;

	if (deviceID >= MAXDEVICES){
		return INVALID_DEVID;
	}

	if (!CheckDeviceOpen(deviceID)){
		return DEVICE_NOT_READY;
	}

	if (!CheckV2Features(deviceID)){		// only attenuators with V2 (or above) firmware support multiple channel operation
		return FEATURE_NOT_SUPPORTED;
	}

	BYTE VNX_ramp[] = { 0, 0, 0, 0, 0, 0 };

	if (mode != 0){
		VNX_ramp[0] = (BYTE)(mode & MODE_SWEEP);	// mode sweep for V2 includes the 0x10 bit
	}
	else {
		VNX_ramp[0] = 0;
	}

	if (TestMode) {
		return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW
	}

	if (Trace_Out > 2)  printf(" sending a  multi channel ramp command = %x\n", VNX_ramp[0]);

	VNX_ramp[5] = (BYTE)chmask;

	if (!SendReport(deviceID, VNX_SWEEP | VNX_SET, VNX_ramp, 6)){
		return BAD_HID_IO;
	}

	return STATUS_OK;
}

// ---------- profile control functions, only supported in V2 devices -----------

// set profile element, 0.05db units
// profile values are cached, and only read when user code asks for them, writes update the cache

LVSTATUS fnLDA_SetProfileElement(DEVID deviceID, int index, int attenuation) {
	int ch = 0;
	int old_element;
	int scaled_atten;
	int max_index;
	int cmdlen;
	int tmp;

	if (deviceID >= MAXDEVICES) {
		return INVALID_DEVID;
	}

	if (!CheckDeviceOpen(deviceID)){
		return DEVICE_NOT_READY;
	}

	if (!CheckV2Features(deviceID))				// only attenuators with V2 firmware support profiles
	{
		return FEATURE_NOT_SUPPORTED;
	}

	max_index = lda[deviceID].ProfileMaxLength - 1;
	if (index < 0 || index > max_index)			// check if the index is valid
	{
		return BAD_PARAMETER;					// the profile index is invalid
	}

	ch = lda[deviceID].Channel;

	if ((attenuation >= lda[deviceID].MinAttenuation) && (attenuation <= lda[deviceID].MaxAttenuation)) {
		// keep the existing cached value in case our IO fails
		// and stash the new value since we are optimistic

		old_element = lda[deviceID].CachedProfileValue[ch];			// keep cached value in case IO fails

		tmp = index << 16;

		lda[deviceID].CachedProfileValue[ch] =  tmp | (attenuation & 0xFFFF);	// and stash the new value (high word is index, low word is value)

		if (TestMode) return STATUS_OK;		// update our caches, but don't talk to the real HW (the TestMode version does not simulate the profile)

	}
	else {
		return BAD_PARAMETER;							// the attenuation value is out of range
	}

	// -- our parameters are good, so lets send them to the hardware

	// -- scale our attenuation value to the units used by this device --
	// As of 6-20-16 we use the device's UnitScale parameter for scaling,
	// and now we are of course scaling from .05 db units
	if (lda[deviceID].UnitScale > 0)
		scaled_atten = attenuation / lda[deviceID].UnitScale;

	// -- construct the command --
	BYTE VNX_command[] = {0, 0, 0, 0, 0, 0, 0};

	if (CheckHiRes(deviceID)) {
		cmdlen = 5;													// HiRes LDA
		VNX_command[3] = (BYTE)((index >> 8) & 0x03);				// the two ls bits are used by devices with long profiles
		VNX_command[4] = (BYTE)index;								// profile element to update
		VNX_command[0] = (BYTE)(scaled_atten & 0x000000ff);			// low byte of attenuation in .05 db device units
		VNX_command[1] = (BYTE)((scaled_atten & 0x0000ff00) >> 8);	// high byte of attenuation
	}
	else {
		cmdlen = 2;													// traditional V2 LDA
		VNX_command[1] = (BYTE)index;								// profile element to update
		VNX_command[2] = (BYTE)scaled_atten;						// attenuation in device units
	}

	if (!SendReport(deviceID, VNX_SETPROFILE | VNX_SET, VNX_command, cmdlen)) {
		// our send failed, we'll leave the cache as we found it
		lda[deviceID].CachedProfileValue[ch] = old_element;
		return BAD_HID_IO;
	}
	return STATUS_OK;
}

// Set the time to remain at each attenuation value in a profile

LVSTATUS fnLDA_SetProfileDwellTime(DEVID deviceID, int dwelltime) {
	int ch = 0;

	if (deviceID >= MAXDEVICES){
		return INVALID_DEVID;
	}

	if (!CheckDeviceOpen(deviceID)){
		return DEVICE_NOT_READY;
	}

	if (!CheckV2Features(deviceID)){		// only attenuators with V2 firmware support profiles
		return FEATURE_NOT_SUPPORTED;
	}

	ch = lda[deviceID].Channel;

	int old_dwelltime = lda[deviceID].ProfileDwellTime[ch];

	if (dwelltime >= VNX_MIN_DWELLTIME) {

		lda[deviceID].ProfileDwellTime[ch] = dwelltime;
		if (TestMode) {
			return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW
		}
	}
	else {
		return BAD_PARAMETER;
	}

	// the profile dwell time value is OK, lets send it to the hardware
	unsigned char *ptr = (unsigned char *) &lda[deviceID].ProfileDwellTime[ch];

	if (!SendReport(deviceID, VNX_PROFILEDWELL | VNX_SET, ptr, 4)){

		lda[deviceID].ProfileDwellTime[ch] = old_dwelltime;
		return BAD_HID_IO;
	}

	return STATUS_OK;
}

// Set the time to wait at the end of a repeating profile

LVSTATUS fnLDA_SetProfileIdleTime(DEVID deviceID, int idletime) {
	int ch = 0;

	if (deviceID >= MAXDEVICES){
		return INVALID_DEVID;
	}

	if (!CheckDeviceOpen(deviceID)){
		return DEVICE_NOT_READY;
	}

	if (!CheckV2Features(deviceID)){		// only attenuators with V2 firmware support profiles
		return FEATURE_NOT_SUPPORTED;
	}

	ch = lda[deviceID].Channel;
	int old_idletime = lda[deviceID].ProfileIdleTime[ch];

	if (idletime >= VNX_MIN_DWELLTIME){

		lda[deviceID].ProfileIdleTime[ch] = idletime;
		if (TestMode){
			return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW
		}
	}
	else
	{
		return BAD_PARAMETER;
	}

	// the profile idle time value is OK, lets send it to the hardware

	unsigned char *ptr = (unsigned char *) &lda[deviceID].ProfileIdleTime[ch];


	if (!SendReport(deviceID, VNX_PROFILEIDLE | VNX_SET, ptr, 4)){

		lda[deviceID].ProfileIdleTime[ch] = old_idletime;
		return BAD_HID_IO;
	}

	return STATUS_OK;
}

// Set the number of elements in the profile
LVSTATUS fnLDA_SetProfileCount(DEVID deviceID, int profilecount) {
	int ch = 0;
	int max_count;

	if (deviceID >= MAXDEVICES){
		return INVALID_DEVID;
	}

	if (!CheckDeviceOpen(deviceID)){
		return DEVICE_NOT_READY;
	}

	if (!CheckV2Features(deviceID)){		// only attenuators with V2 firmware support profiles
		return FEATURE_NOT_SUPPORTED;
	}

	ch = lda[deviceID].Channel;
	int old_count = lda[deviceID].ProfileCount[ch];

	max_count = lda[deviceID].ProfileMaxLength;
	if (profilecount <= max_count && profilecount > 0){

		lda[deviceID].ProfileCount[ch] = profilecount;
		if (TestMode){
			return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW
		}
	}
	else
	{
		return BAD_PARAMETER;
	}

	// the profile count value is OK, lets send it to the hardware
	unsigned char *ptr = (unsigned char *) &lda[deviceID].ProfileCount[ch];

	if (!SendReport(deviceID, VNX_PROFILECOUNT | VNX_SET, ptr, 4)){

		lda[deviceID].ProfileCount[ch] = old_count;
		return BAD_HID_IO;
	}

	return STATUS_OK;
}


// Start the profile immediately
LVSTATUS fnLDA_StartProfile(DEVID deviceID, int mode) {

	if (deviceID >= MAXDEVICES){
		return INVALID_DEVID;
	}

	if (!CheckDeviceOpen(deviceID)){
		return DEVICE_NOT_READY;
	}

	if (!CheckV2Features(deviceID)){		// only attenuators with V2 firmware support profiles
		return FEATURE_NOT_SUPPORTED;
	}
	// -- NB: VNX_command[1] and VNX_command[2] must be 0 for immediate sweep start --
	BYTE VNX_command[] = {0, 0, 0, 0};

	if (mode != 0)	// mode is 1 for a single profile, 2 for a repeating profile
	{
		VNX_command[0] = (BYTE) ((mode & 0x00000003) | STATUS_PROFILE_ACTIVE);	// start the profile
	}
	else
	{
		VNX_command[0] = 0;		// stop the profile
	}

	if (TestMode){
		return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW
	}


	if (!SendReport(deviceID, VNX_SWEEP | VNX_SET, VNX_command, 3))
	{
		return BAD_HID_IO;
	}

	return STATUS_OK;
}

// -- This function uses the multi-channel command capability of V2 firmware devices (also in some earlier devices)
//	  to launch a number of profiles concurrently
//	  chmask has 1 bits for all active channels.
//	  mode is 1 for a single profile, 2 for repeating profiles, and 0 to stop the profiles
//	  the delayed parameter is not supported at this time, set it to false for future compatibility
//
LVSTATUS fnLDA_StartProfileMC(DEVID deviceID, int mode, int chmask, bool delayed)
{
	if (deviceID >= MAXDEVICES) {
		return INVALID_DEVID;
	}

	if (!CheckDeviceOpen(deviceID)) {
		return DEVICE_NOT_READY;
	}

	if (!CheckV2Features(deviceID)) {		// only attenuators with V2 firmware support profiles
		return FEATURE_NOT_SUPPORTED;
	}
	// -- NB: VNX_command[1] and VNX_command[2] must be 0 for immediate sweep start --
	BYTE VNX_command[] = { 0, 0, 0, 0, 0, 0};

	if (mode != 0) {	// mode is 1 for a single profile, 2 for a repeating profile
		VNX_command[0] = (BYTE)((mode & 0x00000003) | STATUS_PROFILE_ACTIVE);	// start the profile
	}
	else {
		VNX_command[0] = 0;		// stop the profile
	}

	if (TestMode) {
		return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW
	}

	VNX_command[5] = (BYTE) chmask;

	if (Trace_Out > 2)  printf(" sending a multi channel start profile command = %x\n", VNX_command[0]);

	if (!SendReport(deviceID, VNX_SWEEP | VNX_SET, VNX_command, 6)) {
		return BAD_HID_IO;
	}

	return STATUS_OK;
}

// Save the user settings to flash for autonomous operation
LVSTATUS fnLDA_SaveSettings(DEVID deviceID) {
  if (deviceID >= MAXDEVICES)
    return INVALID_DEVID;

  if (!CheckDeviceOpen(deviceID))
    return DEVICE_NOT_READY;

  if (TestMode)
    return STATUS_OK;		// in test mode we update our internal variables, but don't talk to the real HW

	// -- protect the LDA-602Q calibration tables on early devices
  if ((lda[deviceID].DevType == LDA_602Q) && (lda[deviceID].SerialNumber < 22160))
	return FEATURE_NOT_SUPPORTED;

  char VNX_savesettings[] = {0x42, 0x55, 0x31}; //three byte key to unlock the user protection.

  if (!SendReport(deviceID, VNX_SAVEPAR | VNX_SET, VNX_savesettings, 3))
    return BAD_HID_IO;

  return STATUS_OK;
}

// ------------- Functions to get parameters ---------------------

// Get the working frequency for HiRes Attenuators in 100 KHz units
int fnLDA_GetWorkingFrequency(DEVID deviceID) {
	int ch = 0;

  if (deviceID >= MAXDEVICES)
    return INVALID_DEVID;

  if (!CheckDeviceOpen(deviceID))
    return DEVICE_NOT_READY;

  if (!CheckHiRes(deviceID)) return BAD_PARAMETER;	// only HiRes devices have a working frequency

  ch = lda[deviceID].Channel;

  if (lda[deviceID].WorkingFrequency[ch] == -1) {		// we don't have a value cached, so go get it.
		if (!GetWorkingFrequency(deviceID)){
			return BAD_HID_IO;
		}
  }

  return lda[deviceID].WorkingFrequency[ch];
}

// Get the attenuation setting in .05db units
int fnLDA_GetAttenuation(DEVID deviceID) {
  int ch = 0;
  if (deviceID >= MAXDEVICES)
    return INVALID_DEVID;

  if (!CheckDeviceOpen(deviceID))
    return DEVICE_NOT_READY;

  ch = lda[deviceID].Channel;

  if (lda[deviceID].Attenuation[ch] == -1) {		// we don't have a value cached, so go get it.
    if (!GetAttenuation(deviceID)){
      return BAD_HID_IO;
    }
  }

  return lda[deviceID].Attenuation[ch];
}

// Get the ramp start attenuation level
int fnLDA_GetRampStart(DEVID deviceID) {
  int ch = 0;
  if (deviceID >= MAXDEVICES)
    return INVALID_DEVID;

  if (!CheckDeviceOpen(deviceID))
    return DEVICE_NOT_READY;

  ch = lda[deviceID].Channel;

  if (lda[deviceID].RampStart[ch] == -1) {		// we don't have a value cached, so go get it.
		if (!GetRampStart(deviceID)){
			return BAD_HID_IO;
		}
  }

  return lda[deviceID].RampStart[ch];
}

// Get the attenuation at the end of the ramp
int fnLDA_GetRampEnd(DEVID deviceID) {
  int ch = 0;
  if (deviceID >= MAXDEVICES)
    return INVALID_DEVID;

  if (!CheckDeviceOpen(deviceID))
    return DEVICE_NOT_READY;

  ch = lda[deviceID].Channel;

  if (lda[deviceID].RampStop[ch] == -1) {		// we don't have a value cached, so go get it.
		if (!GetRampStop(deviceID)){
			return BAD_HID_IO;
		}
  }

  return lda[deviceID].RampStop[ch];
}

// Get the time to dwell at each step along the first phase of the ramp
int fnLDA_GetDwellTime(DEVID deviceID) {
	int ch = 0;

	if (deviceID >= MAXDEVICES)
	  return INVALID_DEVID;

	if (!CheckDeviceOpen(deviceID))
	  return DEVICE_NOT_READY;

	ch = lda[deviceID].Channel;

    if (lda[deviceID].DwellTime[ch] == -1) {		// we don't have a value cached, so go get it.
		if (!GetDwellTime(deviceID)){
			return BAD_HID_IO;
		}
	}
    return lda[deviceID].DwellTime[ch];
}

// Get the time to dwell at each step along the second phase of the ramp
int fnLDA_GetDwellTimeTwo(DEVID deviceID) {
	int ch = 0;

	if (deviceID >= MAXDEVICES)
	  return INVALID_DEVID;

	if (!CheckDeviceOpen(deviceID))
	  return DEVICE_NOT_READY;

	ch = lda[deviceID].Channel;

    if (lda[deviceID].DwellTime2[ch] == -1) {		// we don't have a value cached, so go get it.
		if (!GetDwellTime2(deviceID)){
			return BAD_HID_IO;
		}
	}
    return lda[deviceID].DwellTime2[ch];
}


// Get the idle time to wait at the end of the ramp
int fnLDA_GetIdleTime(DEVID deviceID) {
	int ch = 0;

  if (deviceID >= MAXDEVICES)
    return INVALID_DEVID;

  if (!CheckDeviceOpen(deviceID))
    return DEVICE_NOT_READY;

  ch = lda[deviceID].Channel;

  if (lda[deviceID].IdleTime[ch] == -1) {		// we don't have a value cached, so go get it.
		if (!GetIdleTime(deviceID)){
			return BAD_HID_IO;
		}
  }

  return lda[deviceID].IdleTime[ch];
}

// Get the hold time to wait at the end of the first phase of the ramp
int fnLDA_GetHoldTime(DEVID deviceID) {
	int ch = 0;

  if (deviceID >= MAXDEVICES)
    return INVALID_DEVID;

  if (!CheckDeviceOpen(deviceID))
    return DEVICE_NOT_READY;

  ch = lda[deviceID].Channel;

  if (lda[deviceID].HoldTime[ch] == -1) {		// we don't have a value cached, so go get it.
		if (!GetHoldTime(deviceID)){
			return BAD_HID_IO;
		}
  }

  return lda[deviceID].HoldTime[ch];
}
// Get the size of the attenuation step for the first phase of the ramp in .05db units
int fnLDA_GetAttenuationStep(DEVID deviceID) {
	int ch = 0;

  if (deviceID >= MAXDEVICES)
    return INVALID_DEVID;

  if (!CheckDeviceOpen(deviceID))
    return DEVICE_NOT_READY;

  ch = lda[deviceID].Channel;

    if (lda[deviceID].AttenuationStep[ch] == -1) {		// we don't have a value cached, so go get it.
		if (!GetAttenuationStep(deviceID)){
			return BAD_HID_IO;
		}
  }

  return lda[deviceID].AttenuationStep[ch];
}

int fnLDA_GetAttenuationStepTwo(DEVID deviceID) {
	int ch = 0;

  if (deviceID >= MAXDEVICES)
    return INVALID_DEVID;

  if (!CheckDeviceOpen(deviceID))
    return DEVICE_NOT_READY;

  ch = lda[deviceID].Channel;

    if (lda[deviceID].AttenuationStep2[ch] == -1) {		// we don't have a value cached, so go get it.
		if (!GetAttenuationStep2(deviceID)){
			return BAD_HID_IO;
		}
	}

  return lda[deviceID].AttenuationStep2[ch];
}

// Get functions related to profiles
int fnLDA_GetProfileDwellTime(DEVID deviceID) {
	int ch = 0;

	if (deviceID >= MAXDEVICES){
		return INVALID_DEVID;
	}

	if (!CheckDeviceOpen(deviceID)){
		return DEVICE_NOT_READY;
	}

	if (!CheckV2Features(deviceID)){		// only attenuators with V2 firmware support profiles
		return FEATURE_NOT_SUPPORTED;
	}

	ch = lda[deviceID].Channel;
	if (lda[deviceID].ProfileDwellTime[ch] == -1)	// we don't have a value cached so go get it
		{
			if (!GetProfileDwellTime(deviceID)) {
				return BAD_HID_IO;
			}
		}

	return lda[deviceID].ProfileDwellTime[ch];		// fetch the value for the current channel
}

int fnLDA_GetProfileIdleTime(DEVID deviceID) {
	int ch = 0;

	if (deviceID >= MAXDEVICES){
		return INVALID_DEVID;
	}

	if (!CheckDeviceOpen(deviceID)){
		return DEVICE_NOT_READY;
	}

	if (!CheckV2Features(deviceID)){		// only attenuators with V2 firmware support profiles
		return FEATURE_NOT_SUPPORTED;
	}

	ch = lda[deviceID].Channel;

	if (lda[deviceID].ProfileIdleTime[ch] == -1)	// we don't have a value cached
	{
		if (!GetProfileIdleTime(deviceID)) {
				return BAD_HID_IO;
		}
	}

	return lda[deviceID].ProfileIdleTime[ch];	// fetch the value for the current channel
}

int fnLDA_GetProfileCount(DEVID deviceID) {
	int ch = 0;

	if (deviceID >= MAXDEVICES){
		return INVALID_DEVID;
	}

	if (!CheckDeviceOpen(deviceID)){
		return DEVICE_NOT_READY;
	}

	if (!CheckV2Features(deviceID)){		// only attenuators with V2 firmware support profiles
		return FEATURE_NOT_SUPPORTED;
	}

	ch = lda[deviceID].Channel;

		if (lda[deviceID].ProfileCount[ch] == -1) {	// we don't have a value cached, go get one
			if (!GetProfileCount(deviceID)) {
				return BAD_HID_IO;
			}
		}

	return lda[deviceID].ProfileCount[ch];	// fetch the value for the current channel

}


// get profile element, 0.05db units
int fnLDA_GetProfileElement(DEVID deviceID, int index) {
	int ch = 0;
	int max_index;
	int tmp;

	if (deviceID >= MAXDEVICES){
		return INVALID_DEVID;
	}

	if (!CheckDeviceOpen(deviceID)){
		return DEVICE_NOT_READY;
	}

	if (!CheckV2Features(deviceID)){		// only attenuators with V2 firmware support profiles
		return FEATURE_NOT_SUPPORTED;
	}

	max_index = lda[deviceID].ProfileMaxLength - 1;
	ch = lda[deviceID].Channel;

	if (index < 0 || index > max_index)	{	// check if the index is valid
		return BAD_PARAMETER;
	}
	else {
		// we keep a single profile element cache
		tmp = lda[deviceID].CachedProfileValue[ch] >> 16;		// we keep the index of the cached value in the high 16 bits

		if (tmp == index) return lda[deviceID].CachedProfileValue[ch] & 0xFFFF; // we've got a value cached
		else {
			if (!GetProfileElement(deviceID, index)) {
				return BAD_HID_IO;
			}
			return lda[deviceID].CachedProfileValue[ch] & 0xFFFF;		// we read the value from the HW and cached it.
		}
	}
}

// -- modified to return DATA_UNAVAILABLE if we cannot get the profile index value in a timely fashion
//		for some profile timing the index is not updated during the profile, or for a long time

int fnLDA_GetProfileIndex(DEVID deviceID){
	int ch = 0;
	int timedout;

	if (deviceID >= MAXDEVICES){
		return INVALID_DEVID;
	}

	if (!CheckDeviceOpen(deviceID)){
		return DEVICE_NOT_READY;
	}

	if (!CheckV2Features(deviceID)){		// only attenuators with V2 firmware support profiles
		return FEATURE_NOT_SUPPORTED;
	}

	ch = lda[deviceID].Channel;

	if (lda[deviceID].ProfileIndex[ch] == -1) {
		// we don't have a value cached, we should have one from status reports, but don't, so wait some
		starttime = time(NULL);
		timedout = 0;

		// wait until the device reports its index or 1 second has gone by
		while ((lda[deviceID].ProfileIndex[ch] == -1) && (timedout == 0)) {
	  		catnap(10);	// yield for 10 ms
	  		if ((time(NULL)-starttime) > 1) timedout = 1;
		}

		if (timedout == 1) {
			return DATA_UNAVAILABLE;
		}
	}
	return lda[deviceID].ProfileIndex[ch];
}

// Get the maximum profile length supported by the device
int fnLDA_GetProfileMaxLength(DEVID deviceID)
{
	if (deviceID >= MAXDEVICES)
		return INVALID_DEVID;

	return lda[deviceID].ProfileMaxLength;
}

// Get the state of the RF output - on or off
int fnLDA_GetRF_On(DEVID deviceID) {
	int ch = 0;

  if (deviceID >= MAXDEVICES)
    return INVALID_DEVID;

  if (!CheckDeviceOpen(deviceID))
    return DEVICE_NOT_READY;

  ch = lda[deviceID].Channel;

  if (lda[deviceID].Modebits[ch] & MODE_RFON)
    return 1;
  else
    return 0;
}

// Get the maximum attenuation in .05db units
int fnLDA_GetMaxAttenuation(DEVID deviceID) {

  if (deviceID >= MAXDEVICES)
    return INVALID_DEVID;

  return lda[deviceID].MaxAttenuation;
}

// Get the minimum attenuation in .05db units
int fnLDA_GetMinAttenuation(DEVID deviceID) {

  if (deviceID >= MAXDEVICES)
    return INVALID_DEVID;

  return lda[deviceID].MinAttenuation;
}

// Get the resolution of the attenuator, in .05db units
int fnLDA_GetMinAttenStep(DEVID deviceID) {

  if (deviceID >= MAXDEVICES)
    return INVALID_DEVID;

  return lda[deviceID].MinAttenStep;
}
// This function duplicates GetMinAttenStep, and may be deprecated
int fnLDA_GetDevResolution(DEVID deviceID) {

  if (deviceID >= MAXDEVICES)
    return INVALID_DEVID;

  return lda[deviceID].MinAttenStep;
}

// get the minimum working frequency of the HiRes units
int fnLDA_GetMinWorkingFrequency(DEVID deviceID)
{
	if (deviceID >= MAXDEVICES){ return INVALID_DEVID;}

	return lda[deviceID].MinFrequency;	// even quad devices only have one frequency range for all channels
}

// get the maximum working frequency of the HiRes units
int fnLDA_GetMaxWorkingFrequency(DEVID deviceID)
{
	if (deviceID >= MAXDEVICES){ return INVALID_DEVID;}

	return lda[deviceID].MaxFrequency;		// even quad devices only have one frequency range for all channels
}

// Get a set of bits describing the features available in this attenuator device
int fnLDA_GetFeatures(DEVID deviceID) {
	int temp = DEFAULT_FEATURES;

	if (deviceID >= MAXDEVICES){ return INVALID_DEVID;}

	if (!CheckV2Features(deviceID)){
		return DEFAULT_FEATURES;							// The old attenuators have the default feature set
	}
	else if (CheckHiRes(deviceID)){
		temp = (HAS_HIRES | HAS_BIDIR_RAMPS | HAS_PROFILES);// HiRes has bidirectional ramps, profiles and HiRes
	}
	else{
		temp = (HAS_BIDIR_RAMPS | HAS_PROFILES);			// V2 have bidirectional ramps and profiles
	}

	if (lda[deviceID].NumChannels == 4)    temp |= HAS_4CHANNELS;
  if (lda[deviceID].NumChannels == 8)    temp |= HAS_8CHANNELS;
	if (lda[deviceID].ProfileMaxLength == 1000) temp |= HAS_LONG_PROFILE;

	return temp;
}
